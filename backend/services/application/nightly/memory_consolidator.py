import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from components import (
    MAX_RECALL_CONTENT_CHARS,
    MEMORY_CONSOLIDATE_TARGET_ROWS,
    MEMORY_CONSOLIDATE_TRIGGER_ROWS,
    MEMORY_CONSOLIDATE_WINDOW_ROWS,
    get_logger,
    parse_llm_json,
    session_scope,
)
from modules.memory import Memory
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.memory import (
    KIND_TO_PREFIX,
    RECALL_TAGS,
    backfill_memory_embeddings,
    normalize_recall_context,
    normalize_recall_tags,
)
from services.infrastructure.llm import call_llm_once, resolve_user_llm_config

logger = get_logger(__name__)

_CONSOLIDATE_PROMPT = """You are consolidating a user's recall-pool memories. The user is the sole owner
of these facts; you are merging the most-recent {window} rows into at most {target}
summary rows that preserve every durable fact. Do NOT drop facts — duplicate or merge.

Each summary row MUST use one closed-set tag from:
{tags}

Output JSON only, in this shape:
{{
  "summaries": [
    {{"content": "...", "tags": ["one_allowed_tag"], "context": "short_label"}}
  ]
}}

If a fact is genuinely stale (contradicted by other rows, or so trivial it adds nothing),
or if it matches an anti-pattern (such as default language rules like 'speaks Chinese', the companion's
own persona, transient task progress, or duplicate onboarding profile fields), omit it. When uncertain, keep the fact.
""".format(
    window=MEMORY_CONSOLIDATE_WINDOW_ROWS,
    target=MEMORY_CONSOLIDATE_TARGET_ROWS,
    tags=", ".join(sorted(RECALL_TAGS)),
)

_CONSOLIDATION_LOCKS: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True, slots=True)
class RecallSnapshot:
    id: int
    content: str
    context: str | None
    tags: str | None
    importance: float
    updated_at: datetime

    def prompt_row(self) -> dict[str, int | str | None]:
        return {
            "id": self.id,
            "context": self.context,
            "tags": self.tags,
            "content": self.content,
        }


class RecallReplaceStatus(StrEnum):
    WRITTEN = "written"
    EMPTY_SUMMARIES = "empty_summaries"
    STALE_SNAPSHOT = "stale_snapshot"


@dataclass(frozen=True, slots=True)
class RecallReplaceResult:
    status: RecallReplaceStatus
    written: int = 0


def memory_consolidation_lock(user_id: int) -> asyncio.Lock:
    """返回白天整理与夜间 Stage 2 共用的用户级进程内互斥锁。"""
    return _CONSOLIDATION_LOCKS.setdefault(user_id, asyncio.Lock())


async def load_recall_snapshot(db: AsyncSession, user_id: int, *, limit: int) -> list[RecallSnapshot]:
    """在短会话中读取供一次 LLM 整理使用的版本化 recall 快照。"""
    rows = (
        (
            await db.execute(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.context.like(KIND_TO_PREFIX["recall"] + "%"),
                )
                .order_by(Memory.updated_at.desc(), Memory.id.desc())
                .limit(limit),
            )
        )
        .scalars()
        .all()
    )
    return [
        RecallSnapshot(
            id=row.id,
            content=row.content,
            context=row.context,
            tags=row.tags,
            importance=float(row.importance),
            updated_at=row.updated_at,
        )
        for row in rows
    ]


async def replace_recall_pool(
    user_id: int,
    source_rows: list[RecallSnapshot],
    summaries: list[dict],
) -> RecallReplaceResult:
    """仅在源快照仍完整一致时，以一个短事务删除源 recall 行并写入摘要。"""
    new_rows: list[Memory] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        content_str = (summary.get("content") or "").strip()[:MAX_RECALL_CONTENT_CHARS]
        if not content_str:
            continue
        importance = max(0.1, min(5.0, float(summary.get("importance", 1.0) or 1.0)))
        new_rows.append(
            Memory(
                user_id=user_id,
                content=content_str,
                context=normalize_recall_context(summary.get("context"), default="consolidated"),
                tags=json.dumps(normalize_recall_tags(summary.get("tags"))),
                importance=importance,
            ),
        )

    # 至少写一条 summary 才允许删除源行，避免 LLM 全空 payload 清空 recall pool。
    if not new_rows:
        return RecallReplaceResult(RecallReplaceStatus.EMPTY_SUMMARIES)

    expected_ids = {row.id for row in source_rows}
    if not expected_ids or len(expected_ids) != len(source_rows):
        return RecallReplaceResult(RecallReplaceStatus.STALE_SNAPSHOT)

    # 条件 DELETE 同时承担提交前校验和行锁定；并发编辑若先提交会令对应谓词失配，
    # 若本事务先取得行锁，编辑方只能在本事务提交后看到行已不存在。
    snapshot_predicates = [
        and_(
            Memory.id == row.id,
            Memory.content == row.content,
            Memory.context.is_not_distinct_from(row.context),
            Memory.tags.is_not_distinct_from(row.tags),
            Memory.importance == row.importance,
            Memory.updated_at.is_not_distinct_from(row.updated_at),
        )
        for row in source_rows
    ]
    async with session_scope() as db:
        deleted_ids = set(
            (
                await db.execute(
                    delete(Memory)
                    .where(
                        Memory.user_id == user_id,
                        or_(*snapshot_predicates),
                    )
                    .returning(Memory.id),
                )
            )
            .scalars()
            .all(),
        )
        if deleted_ids != expected_ids:
            await db.rollback()
            return RecallReplaceResult(RecallReplaceStatus.STALE_SNAPSHOT)
        db.add_all(new_rows)
        await db.commit()

    # 摘要行落库后批量补向量，保证合并后的 recall pool 仍是稠密可检索的。
    await backfill_memory_embeddings(user_id, [(row.id, row.content) for row in new_rows])
    return RecallReplaceResult(RecallReplaceStatus.WRITTEN, len(new_rows))


async def maybe_consolidate_one_user(user_id: int) -> bool:
    """recall pool 超过触发阈值时合并；跑了返回 True，否则 False。per-user 节流由调用方负责（cron tick 维护 _LAST_MEMORY_CONSOLIDATE）。"""
    # 互斥覆盖“取快照 → LLM → 短事务校验写入”，只持有进程内锁，不跨 LLM await 占用数据库连接或数据库锁。
    async with memory_consolidation_lock(user_id):
        async with session_scope() as db:
            source_rows = await load_recall_snapshot(db, user_id, limit=MEMORY_CONSOLIDATE_WINDOW_ROWS)
            if len(source_rows) < MEMORY_CONSOLIDATE_TRIGGER_ROWS:
                return False
            llm_cfg = await resolve_user_llm_config(db, user_id)
            if not (llm_cfg.get("api_key") and llm_cfg.get("base_url") and llm_cfg.get("model_name")):
                logger.info("memory_consolidator: skipped, missing llm config", extra={"user_id": user_id})
                return False
        rows_payload = [row.prompt_row() for row in source_rows]

        try:
            content = await call_llm_once(llm_cfg, _CONSOLIDATE_PROMPT, rows_payload, max_output_tokens=2000)
            parsed = parse_llm_json(content)
        except Exception as exc:
            logger.warning("memory_consolidator: llm call failed", extra={"user_id": user_id, "error": str(exc)})
            return False

        if not isinstance(parsed, dict) or not isinstance(parsed.get("summaries"), list):
            logger.info("memory_consolidator: no parseable summaries, skipped", extra={"user_id": user_id})
            return False
        summaries = parsed["summaries"][:MEMORY_CONSOLIDATE_TARGET_ROWS]

        result = await replace_recall_pool(user_id, source_rows, summaries)
        if result.status == RecallReplaceStatus.EMPTY_SUMMARIES:
            logger.warning(
                "memory_consolidator: all summaries empty, source rows kept",
                extra={"user_id": user_id, "summary_count": len(summaries)},
            )
            return False
        if result.status == RecallReplaceStatus.STALE_SNAPSHOT:
            logger.info(
                "memory_consolidator: source snapshot changed, old summaries discarded",
                extra={"user_id": user_id},
            )
            return False

        logger.info(
            "memory_consolidator: consolidated",
            extra={"user_id": user_id, "replaced": len(source_rows), "summaries": result.written},
        )
        return True
