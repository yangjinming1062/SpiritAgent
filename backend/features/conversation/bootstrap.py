"""为每位 onboarding 完成用户一次性生成 ``SYSTEM_PRESET_CATALOG`` 中各预设的系统对话。幂等：已存在的预设跳过。"""

from runtime.presets import SYSTEM_PRESET_CATALOG
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.main_conversation import SPECIAL_KIND, get_or_create_special_conversation
from features.conversation.models import Conversation


async def ensure_system_conversations_for_user(db: AsyncSession, user_id: int) -> None:
    """确保该用户目录内每个预设的系统对话都存在；缺失补齐，不重建。幂等。"""
    existing = (
        (
            await db.execute(
                select(Conversation.system_preset_id).where(
                    Conversation.user_id == user_id,
                    Conversation.kind == SPECIAL_KIND,
                    Conversation.system_preset_id.is_not(None),
                ),
            )
        )
        .scalars()
        .all()
    )
    have = set(existing)

    for preset_id in SYSTEM_PRESET_CATALOG:
        if preset_id in have:
            continue
        await get_or_create_special_conversation(db, user_id, preset_id, commit=False)

    await db.commit()
