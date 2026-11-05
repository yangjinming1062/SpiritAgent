"""在内容发布事务内接入生活事实流。"""

from features.life.schemas import LifeEventInput
from features.life.use_cases import accept_event_in_session
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
