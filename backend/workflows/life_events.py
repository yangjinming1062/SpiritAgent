"""把可持续的生活事实投影到伙伴记忆。

事件表是事实的唯一来源；这里仅建立可召回的系统记忆索引，内容带有事件主体和
来源性质，不能替代事件表或把计划写成已完成经历。
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime

from features.life.ports import LifeEventProjection
from features.life.prompts import life_content_payload
from features.life.schemas import LifeEventInput
from features.life.use_cases import accept_event_in_session, record_events_in_session
from features.memory.contracts import SystemMemoryWrite
from features.memory.use_cases import ensure_system_memories_in_session
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc
from runtime.memory_scope import MemoryScope, MemorySource
from sqlalchemy.ext.asyncio import AsyncSession


def _memory_batch_id(event_key: str) -> str:
    digest = hashlib.sha256(event_key.encode("utf-8")).hexdigest()
    return f"life-event:{digest}"


def _memory_content(event: LifeEventInput, occurred_at: datetime) -> str:
    fact = json.dumps(life_content_payload(event.fact), ensure_ascii=False, separators=(",", ":"))
    result = json.dumps(life_content_payload(event.result_ref), ensure_ascii=False, separators=(",", ":"))
    occurred_at_text = ensure_utc(occurred_at).isoformat()
    subject = {"companion": "伙伴", "user": "用户", "system": "系统"}[event.subject]
    nature = {
        "intent": "意向或计划，尚非执行结果",
        "virtual_activity": "虚拟生活情景，不证明阅读、出行等外部经历",
        "execution": "实际执行状态，须按结果判断是否成功",
        "result": "已确认的执行成果，只证明所列结果",
    }[event.fact_type]
    publication = {"post.published": "发布动态", "diary.published": "写日记"}.get(event.kind, "生活记录")
    return f"{occurred_at_text} {subject}的{publication}。性质：{nature}。内容：{fact}。结果：{result}。"


async def project_life_events_to_memory(db: AsyncSession, user_id: int, events: Sequence[LifeEventProjection]) -> None:
    """事实批量落库后，用一次写入建立全部不可变记忆槽位。"""
    await ensure_system_memories_in_session(
        db,
        MemoryScope(user_id, "companion"),
        [
            SystemMemoryWrite(
                content=_memory_content(item.event, item.occurred_at),
                context="生活记录",
                slot_key=f"life:{item.event.event_key}",
                source_event_id=item.event_id,
                tags=("life", item.event.kind),
                source=MemorySource("interaction", batch_id=_memory_batch_id(item.event.event_key)),
            )
            for item in events
        ],
    )


async def record_post_published(db: AsyncSession, user_id: int, post_id: int) -> None:
    """在动态发布事务内把已发布事实接入生活事件流。"""

    await accept_event_in_session(
        db,
        user_id,
        LifeEventInput(
            event_key=f"post:{post_id}:published",
            kind="post.published",
            source="posts",
            fact_type="result",
            fact={"post_id": post_id},
            result_ref={"entry_id": post_id},
        ),
    )


async def record_diary_published(
    db: AsyncSession,
    user_id: int,
    *,
    entry_date: str,
    title: str,
    diary_id: int,
) -> None:
    """日记和生活事件共用夜间事务。"""

    await accept_event_in_session(
        db,
        user_id,
        LifeEventInput(
            event_key=f"diary:{user_id}:{entry_date}:published",
            kind="diary.published",
            source="journal",
            fact_type="result",
            fact={"entry_date": entry_date, "title": title},
            result_ref={"entry_id": diary_id, "entry_date": entry_date},
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
    events = []
    for action in actions:
        action_key = str(action.get("action_key") or action.get("capability") or "action")
        events.append(
            LifeEventInput(
                event_key=f"nightly:{user_id}:{local_date}:{action_key}",
                kind="nightly.action.completed",
                source="nightly",
                fact_type="result",
                fact={
                    "capability": action.get("capability"),
                    "status": action.get("status"),
                    "fact": action.get("fact"),
                },
                result_ref={"action_key": action_key, "local_date": local_date},
            )
        )
    async with SESSION_LOCAL() as db:
        await record_events_in_session(db, user_id, events)
        await db.commit()
