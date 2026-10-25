"""管理端只返回不含凭据的队列视图，核对操作使用版本锁。"""

from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from infrastructure.redact import redact_sensitive_text
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from runtime.responses import MessageResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.queries import user_ai_configs
from features.media.chain import FrozenMediaProvider

from .contracts import VideoQueueLaneView, VideoQueueList, VideoQueueRequestView, VideoQueueResolution
from .models import ProviderAttempt, ProviderLane
from .service import cancel_video_attempt

logger = get_logger(__name__)


def _request_view(row: ProviderAttempt) -> VideoQueueRequestView:
    return VideoQueueRequestView(
        id=row.token,
        revision=row.revision,
        kind=row.owner_kind,
        job_id=row.job_id,
        user_id=row.user_id,
        model=row.model,
        state=row.state,
        cancel_requested=row.cancel_requested,
        provider_task_id=row.provider_task_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        error_message=row.error_message,
    )


async def list_video_queue(db: AsyncSession) -> VideoQueueList:
    rows = list(
        await db.scalars(
            select(ProviderAttempt)
            .where(
                ProviderAttempt.state.not_in(("succeeded", "failed", "cancelled", "stopped")),
                ProviderAttempt.capability == "video",
            )
            .order_by(ProviderAttempt.enqueued_at, ProviderAttempt.id),
        ),
    )
    if not rows:
        return VideoQueueList(queues=[])
    lanes = {
        row.id: row
        for row in await db.scalars(
            select(ProviderLane).where(
                ProviderLane.id.in_({row.lane_id for row in rows}),
            ),
        )
    }
    names = {provider.id: provider.name for provider in SETTINGS.ai_config.providers}
    configs = await user_ai_configs(db, {row.user_id for row in rows if row.user_id is not None})
    for config in configs.values():
        names.update({provider.id: provider.name for provider in config.providers})
    views: dict[int, VideoQueueLaneView] = {}
    for row in rows:
        lane = lanes[row.lane_id]
        view = views.setdefault(
            lane.id,
            VideoQueueLaneView(
                id=lane.token,
                provider=lane.provider,
                provider_names=[],
                next_retry_at=lane.not_before,
                requests=[],
            ),
        )
        frozen = FrozenMediaProvider.model_validate(row.frozen_json)
        name = names.get(frozen.provider_id)
        if name and name not in view.provider_names:
            view.provider_names.append(name)
        view.requests.append(_request_view(row))
    return VideoQueueList(queues=list(views.values()))


async def resolve_video_queue(
    db: AsyncSession,
    request_id: str,
    payload: VideoQueueResolution,
    admin: str,
) -> VideoQueueRequestView:
    row = await db.scalar(select(ProviderAttempt).where(ProviderAttempt.token == request_id).with_for_update())
    if row is None:
        raise RequestError(status_code=404, message="视频队列任务不存在")
    if row.state != "blocked" or row.revision != payload.expected_revision:
        raise RequestError(status_code=409, message="任务状态已变化，请刷新队列后重试")
    if payload.action == "attach_task" and row.provider_task_id:
        raise RequestError(status_code=409, message="该任务已有远端句柄，请恢复原凭据续查，或核对结束后解除占用")
    row.resolution_note = redact_sensitive_text(payload.note)
    row.resolved_by = admin
    row.revision += 1
    row.updated_at = utc_now()
    if payload.action == "attach_task":
        row.provider_task_id, row.state, row.error_message = payload.task_id, "processing", None
    else:
        row.state, row.request_json, row.error_reason = "stopped", None, "queue_stopped"
        row.error_message = "已核对结束本次视频制作"
        if row.user_id is None or row.job_id is None:
            row.consumed_at = utc_now()
    await db.commit()
    logger.warning(
        "video queue manually reconciled",
        extra={
            "queue_request_id": row.token,
            "action": payload.action,
            "admin": admin,
            "revision": row.revision,
            "note": row.resolution_note,
        },
    )
    return _request_view(row)


async def cancel_video_queue_request(request_id: str, admin: str) -> MessageResponse:
    if not await cancel_video_attempt(request_id):
        raise RequestError(status_code=404, message="视频队列任务不存在")
    logger.warning(
        "video queue manually cancelled",
        extra={"queue_request_id": request_id, "admin": admin},
    )
    return MessageResponse(message="已请求取消该视频任务")


async def retry_video_queue_lane(db: AsyncSession, lane_id: str, admin: str) -> MessageResponse:
    lane = await db.scalar(select(ProviderLane).where(ProviderLane.token == lane_id).with_for_update())
    if lane is None:
        raise RequestError(status_code=404, message="视频队列不存在")
    prior_not_before = lane.not_before
    lane.not_before = None
    await db.commit()
    logger.warning(
        "video queue lane cooldown cleared",
        extra={"lane_token": lane.token, "prior_not_before": prior_not_before, "admin": admin},
    )
    return MessageResponse(message="已清除队列冷却，将按原顺序继续处理")
