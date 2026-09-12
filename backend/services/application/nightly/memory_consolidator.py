import asyncio
import json
from dataclasses import dataclass
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

from services.contracts.memory import EmbeddingItem, MemoryScope, MemorySource
from services.domains.memory import (
    RECALL_TAGS,
    RecallSnapshot,
    backfill_memory_embeddings,
    create_memory,
    load_recall_snapshot,
    normalize_recall_context,
    normalize_recall_tags,
    replace_recall_snapshot,
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

_CONSOLIDATION_LOCKS: dict[MemoryScope, asyncio.Lock] = {}


class RecallReplaceStatus(StrEnum):
    WRITTEN = "written"
    EMPTY_SUMMARIES = "empty_summaries"
    STALE_SNAPSHOT = "stale_snapshot"


@dataclass(frozen=True, slots=True)
class RecallReplaceResult:
    status: RecallReplaceStatus
    written: int = 0


def memory_consolidation_lock(scope: MemoryScope) -> asyncio.Lock:
    """返回白天整理与夜间 Stage 2 共用的作用域进程内互斥锁。"""
    return _CONSOLIDATION_LOCKS.setdefault(scope, asyncio.Lock())


async def replace_recall_pool(
    scope: MemoryScope,
    source_rows: list[RecallSnapshot],
    summaries: list[dict],
) -> RecallReplaceResult:
    valid = [
        item
        for item in summaries
        if isinstance(item, dict) and isinstance(item.get("content"), str) and item["content"].strip()
    ]
    if not valid:
        return RecallReplaceResult(RecallReplaceStatus.EMPTY_SUMMARIES)
    async with session_scope() as db:
        if not await replace_recall_snapshot(db, scope, source_rows):
            return RecallReplaceResult(RecallReplaceStatus.STALE_SNAPSHOT)
        rows = []
        for item in valid:
            row = await create_memory(
                db,
                scope,
                content=item["content"].strip()[:MAX_RECALL_CONTENT_CHARS],
                context=normalize_recall_context(item.get("context"), default="consolidated"),
                tags=json.dumps(normalize_recall_tags(item.get("tags"))),
                importance=max(0.1, min(5.0, float(item.get("importance", 1.0) or 1.0))),
                source=MemorySource(
                    "consolidation",
                    memory_versions=tuple((row.id, row.version) for row in source_rows),
                ),
            )
            rows.append(EmbeddingItem(row.id, row.content, row.content_version))
        await db.commit()
    await backfill_memory_embeddings(scope, rows)
    return RecallReplaceResult(RecallReplaceStatus.WRITTEN, len(rows))


async def maybe_consolidate_one_scope(scope: MemoryScope) -> bool:
    """recall pool 超过触发阈值时合并；跑了返回 True，否则 False。预设级节流由调用方负责（cron tick 维护 _LAST_MEMORY_CONSOLIDATE）。"""
    user_id = scope.user_id
    # 互斥覆盖“取快照 → LLM → 短事务校验写入”，只持有进程内锁，不跨 LLM await 占用数据库连接或数据库锁。
    async with memory_consolidation_lock(scope):
        async with session_scope() as db:
            source_rows = await load_recall_snapshot(db, scope, limit=MEMORY_CONSOLIDATE_WINDOW_ROWS)
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

        result = await replace_recall_pool(scope, source_rows, summaries)
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
