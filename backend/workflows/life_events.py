"""在日记发布事务内接入生活事实流。"""

from features.journal.schemas import DiaryEntryResponse
from features.life.schemas import LifeEventInput
from features.life.use_cases import accept_event_in_session
from sqlalchemy.ext.asyncio import AsyncSession


async def record_diary_published(db: AsyncSession, user_id: int, diary: DiaryEntryResponse) -> None:
    """日记和生活事件共用夜间事务。"""

    await accept_event_in_session(
        db,
        user_id,
        LifeEventInput(
            event_key=f"diary:{user_id}:{diary.entry_date.isoformat()}:published",
            kind="diary.published",
            source="journal",
            fact_type="result",
            shared_with_user=True,
            fact={"entry_date": diary.entry_date.isoformat(), "title": diary.title, "excerpt": diary.body[:240]},
            result_ref={"entry_id": diary.id, "entry_date": diary.entry_date.isoformat()},
        ),
    )
