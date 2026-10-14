"""动态发布历史的只读摘要。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionPost


async def recent_post_summaries(db: AsyncSession, user_id: int, *, limit: int) -> list[tuple[datetime, str, str]]:
    rows = await db.execute(
        select(CompanionPost.published_at, CompanionPost.content_type, CompanionPost.title)
        .where(CompanionPost.user_id == user_id)
        .order_by(CompanionPost.published_at.desc())
        .limit(limit),
    )
    return list(rows.tuples())
