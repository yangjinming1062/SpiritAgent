"""生活记录对共享素材的引用检查。"""

from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeEvent, LifeStep

MaterialKind = Literal["outfit", "scene", "action"]


async def material_has_references(db: AsyncSession, user_id: int, kind: MaterialKind, material_id: int) -> bool:
    """生活计划和结果保留已使用的素材，避免导入撤销破坏历史或待执行事项。"""

    if kind == "action":
        step_filter = or_(LifeStep.business_action_id == material_id, LifeStep.result_action_id == material_id)
        event_filter = LifeEvent.result_action_id == material_id
    elif kind == "outfit":
        step_filter = LifeStep.result_outfit_id == material_id
        event_filter = LifeEvent.result_outfit_id == material_id
    else:
        step_filter = LifeStep.result_scene_id == material_id
        event_filter = LifeEvent.result_scene_id == material_id
    return (
        await db.scalar(select(LifeStep.id).where(LifeStep.user_id == user_id, step_filter).limit(1)) is not None
        or await db.scalar(select(LifeEvent.id).where(LifeEvent.user_id == user_id, event_filter).limit(1)) is not None
    )
