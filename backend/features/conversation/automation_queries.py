"""自动化通过会话所有者创建和核查执行会话。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .main_conversation import STANDARD_KIND
from .models import Conversation


async def create_task_conversation(db: AsyncSession, user_id: int, name: str) -> int:
    conversation = Conversation(
        user_id=user_id,
        kind=STANDARD_KIND,
        title=f"定时任务 · {name}",
        is_automation=True,
        system_preset_id="automation",
    )
    db.add(conversation)
    await db.flush()
    return conversation.id


async def owned_task_conversation_id(db: AsyncSession, user_id: int, conversation_id: int) -> int | None:
    return await db.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.kind == STANDARD_KIND,
            Conversation.is_automation.is_(True),
        ),
    )
