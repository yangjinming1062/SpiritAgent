"""把可持续的生活事实投影到伙伴记忆。

事件表是事实的唯一来源；这里仅建立可召回的系统记忆索引，内容带有事件主体和
来源性质，不能替代事件表或把计划写成已完成经历。
"""

import hashlib
import json
from datetime import datetime

from features.life.schemas import LifeEventInput
from features.life.use_cases import accept_event_in_session
from features.memory.use_cases import ensure_system_memory_in_session
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc
from runtime.memory_scope import MemoryScope, MemorySource
from sqlalchemy.ext.asyncio import AsyncSession


def _memory_context(event_key: str) -> str:
    return f"recall:life:{event_key}"


def _memory_batch_id(event_key: str) -> str:
    digest = hashlib.sha256(event_key.encode("utf-8")).hexdigest()
    return f"life-event:{digest}"


def _memory_content(event: LifeEventInput, occurred_at: datetime) -> str:
    fact = json.dumps(event.fact, ensure_ascii=False, separators=(",", ":"))
    result = json.dumps(event.result_ref, ensure_ascii=False, separators=(",", ":"))
    occurred_at_text = ensure_utc(occurred_at).isoformat()
    subject = {"companion": "伙伴", "user": "用户", "system": "系统"}[event.subject]
    return f"{occurred_at_text} {subject}发生了生活事实「{event.kind}」。事实：{fact}。结果：{result}。"


async def project_life_event_to_memory(
    db: AsyncSession,
    user_id: int,
    event: LifeEventInput,
    occurred_at: datetime,
) -> None:
    """在接纳事件的同一事务中建立幂等的生活记忆。"""

    context = _memory_context(event.event_key)
    scope = MemoryScope(user_id, "companion")
    await ensure_system_memory_in_session(
        db,
        scope,
        content=_memory_content(event, occurred_at),
        context=context,
        tags=json.dumps(["life", event.kind], ensure_ascii=False),
        source=MemorySource("interaction", batch_id=_memory_batch_id(event.event_key)),
    )


async def record_post_published(db: AsyncSession, user_id: int, post_id: str) -> None:
    """在动态发布事务内把已发布事实接入生活事件流。"""

    await accept_event_in_session(
        db,
        user_id,
        LifeEventInput(
            event_key=f"post:{post_id}:published",
            kind="post.published",
            source="posts",
            fact={"post_id": post_id},
            result_ref={"post_id": post_id},
        ),
    )


async def record_diary_published(
    db: AsyncSession,
    user_id: int,
    *,
    entry_date: str,
    title: str,
    diary_id: str,
) -> None:
    """日记和生活事件共用夜间事务。"""

    await accept_event_in_session(
        db,
        user_id,
        LifeEventInput(
            event_key=f"diary:{user_id}:{entry_date}:published",
            kind="diary.published",
            source="journal",
            fact={"entry_date": entry_date, "title": title},
            result_ref={"diary_id": diary_id, "entry_date": entry_date},
        ),
    )


async def record_nightly_actions(
    user_id: int,
    local_date: str,
    actions: list[dict[str, object]],
) -> None:
    """把夜间真正完成的动作写入生活事实流。"""

    if not actions:
        return
    async with SESSION_LOCAL() as db:
        for action in actions:
            action_key = str(action.get("action_key") or action.get("capability") or "action")
            await accept_event_in_session(
                db,
                user_id,
                LifeEventInput(
                    event_key=f"nightly:{user_id}:{local_date}:{action_key}",
                    kind="nightly.action.completed",
                    source="nightly",
                    fact={
                        "capability": action.get("capability"),
                        "status": action.get("status"),
                        "fact": action.get("fact"),
                    },
                    result_ref={"action_key": action_key, "local_date": local_date},
                ),
            )
        await db.commit()
