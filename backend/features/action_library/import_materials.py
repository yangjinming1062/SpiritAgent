"""导入来源的动作撤销；已有动作和其他来源的动作不受影响。"""

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


async def undo_imported_action(db: AsyncSession, user_id: int, material_id: int, source_key: str) -> None:
    row = await db.scalar(
        select(ActionDefinition).where(ActionDefinition.user_id == user_id, ActionDefinition.id == material_id)
    )
    if row is None:
        return
    if row.source_key != source_key:
        raise ValueError("不能撤销并非由这次影像提取创建的动作")
    await delete_action(db, user_id, material_id)
