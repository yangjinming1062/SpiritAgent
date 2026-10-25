"""动作素材的受理、编辑与评审状态维护。

登记定义本身不启动图片或视频制作；名称不承担跨来源唯一身份。
"""

from datetime import datetime

from infrastructure.config import SETTINGS
from infrastructure.domain_locks import lock_domain
from infrastructure.text import ensure_utc
from runtime.events.emit import emit_ws_event
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ActionDefinition
from .schemas import ActionAssetDesign, ActionAssetSource, ActionAssetStatus, ActionAssetView
from .views import definition_view, normalize_action_name


async def create_action(
    db: AsyncSession,
    user_id: int,
    design: ActionAssetDesign,
    *,
    source: ActionAssetSource,
    review_source_json: dict,
) -> ActionAssetView:
    """登记一份新的动作素材；评审由 review 模块异步执行。"""

    await lock_domain(db, user_id, "actions")
    if source == "autonomous" and not SETTINGS.action_autocreate_enabled:
        raise ValueError("伙伴自主新增动作已关闭")
    existing = await db.scalars(
        select(ActionDefinition).where(
            ActionDefinition.user_id == user_id,
            ActionDefinition.normalized_name == normalize_action_name(design.name),
        ),
    )
    for item in existing:
        if (
            item.motion_description == design.motion_description
            and item.clip_kind == design.clip_kind
            and item.duration_seconds == design.duration_seconds
            and item.use_when_json == design.use_when
            and item.avoid_when_json == design.avoid_when
            and item.allowed_triggers_json == design.triggers
        ):
            return definition_view(item)
    row = ActionDefinition(
        user_id=user_id,
        name=design.name,
        normalized_name=normalize_action_name(design.name),
        motion_description=design.motion_description,
        use_when_json=list(design.use_when),
        avoid_when_json=list(design.avoid_when),
        allowed_triggers_json=list(design.triggers),
        clip_kind=design.clip_kind,
        duration_seconds=design.duration_seconds,
        reason=design.reason,
        source=source,
        status="pending",
        review_reason="",
        review_source_json=review_source_json,
    )
    db.add(row)
    await db.flush()
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return definition_view(row)


async def ensure_initial_action(db: AsyncSession, user_id: int, *, review_source_json: dict) -> ActionAssetView:
    existing = await db.scalar(
        select(ActionDefinition)
        .where(
            ActionDefinition.user_id == user_id,
            ActionDefinition.status == "approved",
        )
        .order_by(ActionDefinition.id)
        .limit(1),
    )
    if existing is not None:
        return definition_view(existing)
    design = ActionAssetDesign(
        name="安静相伴",
        motion_description="结合角色身体结构，在环境中保持有稳定支撑的舒适姿态，安静相伴，保留自然的局部微小呼吸。",
        clip_kind="loop",
        duration_seconds=4,
        allowed_triggers=["chat", "manual", "periodic", "user_return"],
        use_when=["安静陪伴"],
    )
    view = await create_action(db, user_id, design, source="preset", review_source_json=review_source_json)
    row = await db.get(ActionDefinition, view.id)
    if row is None:
        raise ValueError("初始动作素材创建失败")
    row.status = "approved"
    await db.flush()
    return definition_view(row)


async def update_action(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    design: ActionAssetDesign,
    *,
    expected_updated_at: datetime,
    review_source_json: dict,
) -> ActionAssetView | None:
    """编辑动作语义；内容变化后回到待评审，不追溯已生成影像。"""

    await lock_domain(db, user_id, "actions")
    row = await db.scalar(
        select(ActionDefinition).where(ActionDefinition.user_id == user_id, ActionDefinition.id == action_id),
    )
    if row is None:
        return None
    if ensure_utc(row.updated_at) != ensure_utc(expected_updated_at):
        raise ValueError("动作素材已更新，请保留修改并刷新核对后重试")
    row.name = design.name
    row.normalized_name = normalize_action_name(design.name)
    row.motion_description = design.motion_description
    row.use_when_json = list(design.use_when)
    row.avoid_when_json = list(design.avoid_when)
    row.allowed_triggers_json = list(design.triggers)
    row.clip_kind = design.clip_kind
    row.duration_seconds = design.duration_seconds
    row.reason = design.reason
    row.status = "pending"
    row.review_reason = ""
    row.review_source_json = review_source_json
    await db.flush()
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return definition_view(row)


async def delete_action(db: AsyncSession, user_id: int, action_id: int) -> bool:
    await lock_domain(db, user_id, "actions")
    result = await db.execute(
        delete(ActionDefinition).where(ActionDefinition.user_id == user_id, ActionDefinition.id == action_id),
    )
    if not result.rowcount:
        return False
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return True


async def begin_review(db: AsyncSession, user_id: int, action_id: int, *, expected_updated_at: datetime) -> bool:
    row = await db.scalar(
        select(ActionDefinition).where(ActionDefinition.user_id == user_id, ActionDefinition.id == action_id),
    )
    if row is None or row.status != "pending" or ensure_utc(row.updated_at) != ensure_utc(expected_updated_at):
        return False
    row.status = "reviewing"
    await db.flush()
    return True


async def record_review(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    *,
    decision: str,
    reason: str,
) -> ActionAssetStatus | None:
    """落库评审结论；approve/reject 为终态，defer 等待显式重试。"""

    await lock_domain(db, user_id, "actions")
    row = await db.scalar(
        select(ActionDefinition).where(ActionDefinition.user_id == user_id, ActionDefinition.id == action_id),
    )
    if row is None:
        return None
    mapping: dict[str, ActionAssetStatus] = {"approve": "approved", "defer": "deferred", "reject": "rejected"}
    if row.status != "reviewing":
        return row.status
    status = mapping.get(decision)
    if status is None:
        raise ValueError(f"未知的动作评审结论：{decision}")
    row.status = status
    row.review_reason = reason
    await db.flush()
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return status


async def recover_interrupted_reviews(db: AsyncSession, user_id: int | None = None) -> int:
    """中断的评审转为需手动处理的暂缓，不自动重新付费。"""

    statement = select(ActionDefinition).where(ActionDefinition.status == "reviewing")
    if user_id is not None:
        statement = statement.where(ActionDefinition.user_id == user_id)
    result = await db.execute(statement)
    count = 0
    for row in result.scalars():
        row.status = "deferred"
        row.review_reason = "评审中断，请手动重试"
        count += 1
    if count:
        await db.flush()
    return count
