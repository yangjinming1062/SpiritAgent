from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.references import message_asset_paths

from .models import Conversation, Message


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for message in await db.scalars(select(Message).join(Conversation).where(Conversation.user_id == user_id)):
        referenced |= message_asset_paths(message, user_id)
    return referenced
