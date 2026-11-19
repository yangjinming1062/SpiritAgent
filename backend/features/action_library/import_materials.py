"""导入来源的动作撤销；已有动作和其他来源的动作不受影响。"""

from collections.abc import Awaitable, Callable

from infrastructure.domain_locks import lock_domain
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ActionDefinition
from .service import delete_action


async def action_is_imported(db: AsyncSession, user_id: int, material_id: int, source_key: str) -> bool:
    return (
        await db.scalar(
            select(ActionDefinition.id).where(
                ActionDefinition.user_id == user_id,
                ActionDefinition.id == material_id,
                ActionDefinition.source_key == source_key,
            )
        )
        is not None
    )


async def undo_imported_action(
    db: AsyncSession, user_id: int, material_id: int, source_key: str, still_referenced: Callable[[], Awaitable[bool]]
) -> bool:
    """在来源素材锁内核对引用，返回是否因仍被使用而保留。"""
    await lock_domain(db, user_id, "actions")
    row = await db.scalar(
        select(ActionDefinition)
        .where(ActionDefinition.user_id == user_id, ActionDefinition.id == material_id)
        .with_for_update(of=ActionDefinition)
    )
    if row is None:
        return False
    if row.source_key != source_key:
        raise ValueError("不能撤销并非由这次影像提取创建的动作")
    if await still_referenced():
        return True
    await delete_action(db, user_id, material_id)
    return False
