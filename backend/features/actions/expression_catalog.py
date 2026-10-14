"""伙伴推理读取的表达动作快照；ORM 查询与可用资格归动作模块。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from .policy import is_expression_action
from .repository import get_active_pack, list_pack_actions
from .usage import action_prompt_entry


class ExpressionActionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: int
    media_type: Literal["image", "video"]
    name: str
    motion_description: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    kind: Literal["once", "loop"] | None = None
    duration_seconds: float | None = None


async def available_expression_snapshots(db: AsyncSession, user_id: int) -> tuple[ExpressionActionSnapshot, ...]:
    pack = await get_active_pack(db, user_id)
    if pack is None:
        return ()
    snapshots = [
        ExpressionActionSnapshot.model_validate(action_prompt_entry(row))
        for row in await list_pack_actions(db, pack.id, enabled_only=True)
        if is_expression_action(row)
    ]
    return tuple(sorted(snapshots, key=lambda row: row.action_id))


async def available_expression_prompt_entries(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    return [
        row.model_dump(mode="json", exclude_unset=True) for row in await available_expression_snapshots(db, user_id)
    ]


async def has_active_pack(db: AsyncSession, user_id: int) -> bool:
    return await get_active_pack(db, user_id) is not None
