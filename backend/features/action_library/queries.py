"""动作素材的公开只读查询。"""

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from .models import ActionDefinition
from .schemas import ActionAssetStatus, ActionAssetView, ActionLibraryGroup, ActionListResponse
from .views import definition_view, normalize_action_name


def _filters(user_id: int, query: str | None, clip_kind: str | None, status: str | None) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [ActionDefinition.user_id == user_id]
    needle = normalize_action_name(query or "")
    if needle:
        conditions.append(
            or_(
                ActionDefinition.normalized_name.contains(needle, autoescape=True),
                ActionDefinition.motion_description.icontains((query or "").strip(), autoescape=True),
            ),
        )
    if clip_kind:
        conditions.append(ActionDefinition.clip_kind == clip_kind)
    if status:
        conditions.append(ActionDefinition.status == status)
    return conditions


async def action_detail(db: AsyncSession, user_id: int, action_id: int) -> ActionAssetView | None:
    row = await db.scalar(
        select(ActionDefinition)
        .options(defer(ActionDefinition.review_source_json, raiseload=True))
        .where(ActionDefinition.user_id == user_id, ActionDefinition.id == action_id),
    )
    return definition_view(row) if row else None


async def action_by_preset_key(db: AsyncSession, user_id: int, preset_key: str) -> ActionAssetView | None:
    """按内置动作稳定键取定义；供基础动态准备等固定流程使用。"""

    row = await db.scalar(
        select(ActionDefinition)
        .options(defer(ActionDefinition.review_source_json, raiseload=True))
        .where(ActionDefinition.user_id == user_id, ActionDefinition.preset_key == preset_key),
    )
    return definition_view(row) if row else None


async def action_preset_keys(db: AsyncSession, user_id: int, action_ids: list[int]) -> dict[int, str]:
    """批量取动作的内置稳定键；跨业务只读投影，不加载定义正文。"""

    if not action_ids:
        return {}
    rows = await db.execute(
        select(ActionDefinition.id, ActionDefinition.preset_key).where(
            ActionDefinition.user_id == user_id, ActionDefinition.id.in_(action_ids)
        )
    )
    return {action_id: preset_key for action_id, preset_key in rows.all() if preset_key is not None}


async def list_actions(
    db: AsyncSession,
    user_id: int,
    *,
    query: str | None = None,
    clip_kind: str | None = None,
    status: ActionAssetStatus | None = None,
    group: ActionLibraryGroup = "in_library",
    cursor: int | None = None,
    include_total: bool = False,
    limit: int = 20,
) -> ActionListResponse:
    """按名称或动作内容分页检索；名称只用于检索，不作身份判别。"""

    conditions = _filters(user_id, query, clip_kind, status)
    conditions.append(
        ActionDefinition.status == "approved" if group == "in_library" else ActionDefinition.status != "approved"
    )
    total = (
        (await db.scalar(select(func.count()).select_from(ActionDefinition).where(*conditions)) or 0)
        if include_total
        else None
    )
    if cursor is not None:
        conditions.append(ActionDefinition.id < cursor)
    limit = max(1, min(100, limit))
    rows = list(
        await db.scalars(
            select(ActionDefinition)
            .options(defer(ActionDefinition.review_source_json, raiseload=True))
            .where(*conditions)
            .order_by(ActionDefinition.id.desc())
            .limit(limit + 1),
        )
    )
    return ActionListResponse(
        actions=[definition_view(row) for row in rows[:limit]],
        has_more=len(rows) > limit,
        next_cursor=str(rows[limit - 1].id) if len(rows) > limit else None,
        total=total,
        limit=limit,
    )
