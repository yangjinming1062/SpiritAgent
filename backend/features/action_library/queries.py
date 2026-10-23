"""动作素材的公开只读查询。"""

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ActionDefinition
from .schemas import ActionAssetView, ActionListResponse
from .views import definition_view, normalize_action_name


def _filters(user_id: int, query: str | None, clip_kind: str | None, status: str | None) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [ActionDefinition.user_id == user_id]
    needle = normalize_action_name(query or "")
    if needle:
        conditions.append(
            or_(
                ActionDefinition.normalized_name.like(f"%{needle}%"),
                ActionDefinition.motion_description.ilike(f"%{(query or '').strip()}%"),
            ),
        )
    if clip_kind:
        conditions.append(ActionDefinition.clip_kind == clip_kind)
    if status:
        conditions.append(ActionDefinition.status == status)
    return conditions


async def action_detail(db: AsyncSession, user_id: int, action_id: int) -> ActionAssetView | None:
    row = await db.scalar(
        select(ActionDefinition).where(ActionDefinition.user_id == user_id, ActionDefinition.id == action_id),
    )
    return definition_view(row) if row else None


async def list_actions(
    db: AsyncSession,
    user_id: int,
    *,
    query: str | None = None,
    clip_kind: str | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> ActionListResponse:
    """按名称或动作内容分页检索；名称只用于检索，不作身份判别。"""

    conditions = _filters(user_id, query, clip_kind, status)
    total = await db.scalar(select(func.count()).select_from(ActionDefinition).where(*conditions)) or 0
    rows = await db.scalars(
        select(ActionDefinition).where(*conditions).order_by(ActionDefinition.id.desc()).offset(offset).limit(limit),
    )
    return ActionListResponse(
        actions=[definition_view(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )
