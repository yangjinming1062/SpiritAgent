"""将已发布内容和已完成夜间动作接入生活事实流。"""

from features.life.schemas import LifeEventInput
from features.life.use_cases import accept_event_in_session, record_events_in_session
from infrastructure.database import SESSION_LOCAL
from sqlalchemy.ext.asyncio import AsyncSession


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


async def record_diary_published(db: AsyncSession, user_id: int, *, entry_date: str, title: str, diary_id: int) -> None:
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


async def record_nightly_actions(user_id: int, local_date: str, actions: list[dict[str, object]]) -> None:
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
