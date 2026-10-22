"""按供应商与实际凭据串行受理远端视频，业务消费不占名额。"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from infrastructure.ai.error_classifier import ClassifiedError, FailoverReason, LLMRuntimeError, classify_api_error
from infrastructure.ai.llm_client import build_provider, resolve_model_config
from infrastructure.ai.llm_fallback import execute_with_fallback
from infrastructure.ai.providers.base import (
    ProviderConfig,
    ServiceType,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobStatus,
)
from infrastructure.ai.providers.http import ProviderResultUnknownError
from infrastructure.database import ENGINE, SESSION_LOCAL
from infrastructure.hashing import sha256_hex
from infrastructure.logger import get_logger
from infrastructure.redact import redact_sensitive_text
from infrastructure.text import utc_now
from pydantic import TypeAdapter
from runtime.background import BackgroundTask, TaskBag
from runtime.paid_work import GenerationWorkPaused
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from features.media.chain import FrozenMediaProvider, resolve_frozen_media_provider, video_provider_failure_reason
from features.media.video_polling import poll_video_task

from .contracts import VideoQueueInfo, VideoQueueKind, VideoQueueOwner, VideoQueueStoppedError
from .models import VideoQueueLane, VideoQueueRequest
from .ports import inspect_owner

logger = get_logger(__name__)
_ACTIVE = ("claimed", "submitting", "processing", "blocked")
_PENDING = ("queued", "rate_limited", "paused")
_TERMINAL = ("succeeded", "failed", "cancelled", "stopped")
_REQUEST = TypeAdapter(VideoGenRequest)
_STATUS = TypeAdapter(VideoJobStatus)
_DISPATCHER = BackgroundTask("media.video_queue.dispatcher")
_WORK = TaskBag("media.video_queue.workers")
_LANES: dict[int, asyncio.Task[None]] = {}
_CONFIGS: dict[str, ProviderConfig] = {}
_STOPPING = False
_LEADER: AsyncConnection | None = None
_LEADER_KEY = 0x564944454F515545


def _diagnostic(request_id: str, message: str | None) -> str | None:
    config = _CONFIGS.get(request_id)
    if message is not None and config is not None and config.api_key:
        message = message.replace(config.api_key, "***")
    return redact_sensitive_text(message)


def _owner(row: VideoQueueRequest) -> VideoQueueOwner | None:
    if row.user_id is None or row.entity_id is None:
        return None
    return VideoQueueOwner(kind=row.kind, user_id=row.user_id, entity_id=row.entity_id, generation_id=row.generation_id)


async def _lane_id(config: ProviderConfig) -> int:
    async with SESSION_LOCAL() as db:
        await db.execute(
            insert(VideoQueueLane)
            .values(
                token=uuid4().hex,
                provider=config.provider_name,
                credential_fingerprint=sha256_hex(config.api_key),
            )
            .on_conflict_do_nothing(constraint="uq_video_queue_credential"),
        )
        lane_id = await db.scalar(
            select(VideoQueueLane.id).where(
                VideoQueueLane.provider == config.provider_name,
                VideoQueueLane.credential_fingerprint == sha256_hex(config.api_key),
            ),
        )
        await db.commit()
    if lane_id is None:
        raise RuntimeError("Video queue lane was not created")
    return lane_id


async def _read(request_id: str) -> tuple[VideoQueueRequest, VideoQueueLane]:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VideoQueueRequest).where(VideoQueueRequest.token == request_id))
        if row is None:
            raise VideoQueueStoppedError("视频制作任务已结束")
        lane = await db.get(VideoQueueLane, row.lane_id)
        if lane is None:
            raise RuntimeError("Video queue lane is missing")
        return row, lane


async def _change(request_id: str, *, expected: tuple[str, ...] | None = None, **values: object) -> bool:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VideoQueueRequest).where(VideoQueueRequest.token == request_id).with_for_update())
        if row is None or (expected is not None and row.state not in expected):
            return False
        for key, value in values.items():
            setattr(row, key, value)
        row.revision += 1
        row.updated_at = utc_now()
        await db.commit()
    return True


async def enqueue_video_attempt(
    owner: VideoQueueOwner,
    frozen: FrozenMediaProvider,
    attempt_index: int,
    request: VideoGenRequest | None,
    *,
    provider_task_id: str | None = None,
) -> str:
    """同一尝试只登记一次；已有句柄直接进入查询，不重新提交制作。"""
    if request is None and not provider_task_id:
        raise ValueError("视频尝试须有制作输入或已知供应商句柄")
    config = await resolve_frozen_media_provider(owner.user_id, "video_gen", frozen)
    if config is None:
        raise VideoQueueStoppedError("视频服务配置已变更，请重新发起制作")
    lane_id = await _lane_id(config)
    async with SESSION_LOCAL() as db:
        await db.execute(
            insert(VideoQueueRequest)
            .values(
                token=uuid4().hex,
                lane_id=lane_id,
                user_id=owner.user_id,
                kind=owner.kind,
                entity_id=owner.entity_id,
                generation_id=owner.generation_id,
                attempt_index=attempt_index,
                frozen_json=frozen.model_dump_json(),
                request_json=_REQUEST.dump_json(request).decode() if request is not None else None,
                model=frozen.model,
                state="processing" if provider_task_id else "queued",
                provider_task_id=provider_task_id,
                revision=0,
                enqueued_at=utc_now(),
                cancel_requested=False,
                pause_requested=False,
                rate_limit_count=0,
            )
            .on_conflict_do_nothing(constraint="uq_video_queue_attempt"),
        )
        row = await db.scalar(
            select(VideoQueueRequest)
            .where(
                VideoQueueRequest.generation_id == owner.generation_id,
                VideoQueueRequest.attempt_index == attempt_index,
            )
            .with_for_update(),
        )
        if row is None or _owner(row) != owner or row.frozen_json != frozen.model_dump_json():
            raise VideoQueueStoppedError("视频制作任务的归属或冻结配置不一致")
        if provider_task_id:
            if row.provider_task_id and row.provider_task_id != provider_task_id:
                raise VideoQueueStoppedError("原视频任务句柄不一致，请先核对制作结果")
            if not row.provider_task_id:
                if row.state in _TERMINAL:
                    raise VideoQueueStoppedError("原视频尝试已结束，请先核对制作结果")
                row.provider_task_id, row.request_json = provider_task_id, None
                row.state, row.pause_requested = "processing", False
                row.revision += 1
            elif row.state == "paused":
                row.state, row.pause_requested = "processing", False
                row.revision += 1
        elif row.state == "paused":
            row.state, row.pause_requested, row.enqueued_at = "queued", False, utc_now()
            row.revision += 1
        await db.commit()
        return row.token


def _queue_info(row: VideoQueueRequest, lane: VideoQueueLane) -> VideoQueueInfo | None:
    if row.state == "blocked":
        return VideoQueueInfo(state="blocked")
    if row.state in _PENDING:
        if lane.not_before and lane.not_before > utc_now():
            return VideoQueueInfo(state="rate_limited", next_retry_at=lane.not_before)
        return VideoQueueInfo(state="waiting")
    return None


async def get_video_attempt_status(request_id: str) -> VideoJobStatus | None:
    row, _lane = await _read(request_id)
    return _status(row)


def _status(row: VideoQueueRequest) -> VideoJobStatus | None:
    if row.state in ("stopped", "cancelled") or (row.cancel_requested and row.state in _TERMINAL):
        raise VideoQueueStoppedError("本次视频制作已结束，请按需重新发起")
    if row.result_json:
        return _STATUS.validate_json(row.result_json)
    if row.state == "failed":
        try:
            reason = FailoverReason(row.error_reason or "unknown")
        except ValueError:
            reason = FailoverReason.unknown
        raise LLMRuntimeError(ClassifiedError(reason, row.error_status_code, row.error_message or "视频提交失败"))
    return None


async def wait_video_attempt(
    request_id: str,
    *,
    on_submitted: Callable[[str], Awaitable[None]] | None = None,
    on_queue: Callable[[VideoQueueInfo | None], Awaitable[None]] | None = None,
) -> VideoJobStatus:
    """等待者取消不取消队列所有的远端工作；仅明确取消接口改变远端生命周期。"""
    previous: VideoQueueInfo | None = None
    notified = False
    submitted = ""
    while not _STOPPING:
        try:
            row, lane = await _read(request_id)
        except SQLAlchemyError:
            logger.warning("video queue state read awaits retry", extra={"queue_request_id": request_id})
            await asyncio.sleep(1)
            continue
        if row.state in ("stopped", "cancelled"):
            raise VideoQueueStoppedError("本次视频制作已结束，请按需重新发起")
        if row.state not in _TERMINAL and row.user_id is not None and is_user_in_maintenance(row.user_id):
            raise GenerationWorkPaused
        info = _queue_info(row, lane)
        try:
            if on_queue is not None and (not notified or info != previous):
                await on_queue(info)
                previous, notified = info, True
            if row.provider_task_id and submitted != row.provider_task_id:
                if on_submitted is not None:
                    await on_submitted(row.provider_task_id)
                submitted = row.provider_task_id
        except GenerationWorkPaused:
            raise
        except Exception:
            # 业务进度写回失败不能变成供应商失败并引出另一笔付费提交。
            logger.warning("video queue delivery awaits retry", extra={"queue_request_id": request_id}, exc_info=True)
            await asyncio.sleep(1)
            continue
        if row.state == "paused" or (row.pause_requested and row.state not in _TERMINAL):
            raise GenerationWorkPaused
        result = _status(row)
        if result is not None:
            return result
        await asyncio.sleep(0.5)
    raise asyncio.CancelledError


async def acknowledge_video_attempt(request_id: str) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VideoQueueRequest).where(VideoQueueRequest.token == request_id).with_for_update())
        if row is None or row.state not in _TERMINAL or row.consumed_at is not None:
            return
        row.consumed_at, row.request_json = utc_now(), None
        row.revision += 1
        await db.commit()


async def cancel_video_attempt(request_id: str) -> bool:
    """幂等请求取消，返回任务是否存在；远端占用保留到确定终态。"""
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VideoQueueRequest).where(VideoQueueRequest.token == request_id).with_for_update())
        if row is None:
            return False
        if row.state in _TERMINAL or row.cancel_requested:
            return True
        row.cancel_requested, row.request_json = True, None
        if row.state in _PENDING or row.state == "claimed":
            row.state = "cancelled"
        row.revision += 1
        row.updated_at = utc_now()
        await db.commit()
    return True


async def pause_video_attempt(request_id: str) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VideoQueueRequest).where(VideoQueueRequest.token == request_id).with_for_update())
        if row is None or row.state in _TERMINAL:
            return
        row.pause_requested = True
        if row.state in _PENDING or row.state == "claimed":
            row.state = "paused"
        row.revision += 1
        row.updated_at = utc_now()
        await db.commit()


async def cancel_owner_video_attempts(
    kind: VideoQueueKind,
    entity_id: int,
    generation_id: str | None = None,
) -> None:
    async with SESSION_LOCAL() as db:
        query = select(VideoQueueRequest.token).where(
            VideoQueueRequest.kind == kind,
            VideoQueueRequest.entity_id == entity_id,
            VideoQueueRequest.state.not_in(_TERMINAL),
        )
        if generation_id is not None:
            query = query.where(VideoQueueRequest.generation_id == generation_id)
        tokens = list(await db.scalars(query))
    for token in tokens:
        await cancel_video_attempt(token)


async def anonymize_user_video_queue(user_id: int) -> None:
    """清除用户内容，远端未决占用留到确认结束，不能随账户级联删除。"""
    async with SESSION_LOCAL() as db:
        tokens = list(await db.scalars(select(VideoQueueRequest.token).where(VideoQueueRequest.user_id == user_id)))
    for token in tokens:
        await cancel_video_attempt(token)
        async with SESSION_LOCAL() as db:
            row = await db.scalar(select(VideoQueueRequest).where(VideoQueueRequest.token == token).with_for_update())
            if row is None:
                continue
            row.user_id, row.entity_id = None, None
            row.request_json, row.result_json, row.resolution_note = None, None, None
            row.error_reason, row.error_status_code, row.error_message = None, None, None
            row.consumed_at = utc_now() if row.state in _TERMINAL else None
            row.revision += 1
            row.updated_at = utc_now()
            await db.commit()


async def _readiness(row: VideoQueueRequest, *, before_submit: bool = True) -> str:
    owner = _owner(row)
    if owner is None:
        return "gone"
    if is_user_in_maintenance(owner.user_id):
        return "paused"
    return await inspect_owner(owner, before_submit, row.token)


async def _config(row: VideoQueueRequest, lane: VideoQueueLane) -> ProviderConfig | None:
    remembered = _CONFIGS.get(row.token) if row.state in _ACTIVE else None
    if remembered is not None:
        return remembered
    if row.user_id is None:
        return None
    frozen = FrozenMediaProvider.model_validate_json(row.frozen_json)
    if row.cancel_requested and row.entity_id is None:
        async with SESSION_LOCAL() as db:
            current = await resolve_model_config(
                db,
                row.user_id,
                ServiceType.video_gen,
                frozen.model_card_id,
                frozen.provider_id,
            )
        if current is not None and (current.provider_name != frozen.provider or current.base_url != frozen.base_url):
            return None
        if current is not None:
            current = replace(current, model=frozen.model)
    else:
        current = await resolve_frozen_media_provider(row.user_id, "video_gen", frozen)
    if current is None:
        return None
    if row.state in _ACTIVE and sha256_hex(current.api_key) != lane.credential_fingerprint:
        return None
    return current


async def _finish(request_id: str, status: VideoJobStatus) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VideoQueueRequest).where(VideoQueueRequest.token == request_id).with_for_update())
        if row is None or row.state not in _ACTIVE or row.provider_task_id != status.task_id:
            return
        row.request_json = None
        if row.cancel_requested or row.user_id is None or row.entity_id is None:
            row.state, row.result_json = "cancelled", None
            row.error_reason, row.error_status_code, row.error_message = None, None, None
            if row.user_id is None or row.entity_id is None:
                row.consumed_at = utc_now()
        else:
            status = replace(status, error=_diagnostic(row.token, status.error))
            row.state = "succeeded" if status.status == "succeeded" else "failed"
            row.result_json = _STATUS.dump_json(status).decode()
            row.error_reason = video_provider_failure_reason(status.error) if status.status == "failed" else None
            row.error_message = redact_sensitive_text(status.error)
        row.revision += 1
        row.updated_at = utc_now()
        await db.commit()
    _CONFIGS.pop(request_id, None)


async def _block(request_id: str, message: str) -> None:
    await _change(
        request_id,
        expected=_ACTIVE,
        state="blocked",
        error_reason="result_unknown",
        error_message=_diagnostic(request_id, message),
    )
    row, _lane = await _read(request_id)
    if row.provider_task_id:
        async with SESSION_LOCAL() as db:
            lane = await db.get(VideoQueueLane, row.lane_id, with_for_update=True)
            if lane is not None:
                lane.not_before = max(lane.not_before or utc_now(), utc_now() + timedelta(seconds=60))
                await db.commit()


async def _select_lane_request(lane_id: int) -> tuple[VideoQueueRequest, VideoQueueLane] | None:
    async with SESSION_LOCAL() as db:
        lane = await db.get(VideoQueueLane, lane_id, with_for_update=True)
        if lane is None:
            return None
        if lane.not_before and lane.not_before > utc_now():
            return None
        active = await db.scalar(
            select(VideoQueueRequest)
            .where(
                VideoQueueRequest.lane_id == lane_id,
                VideoQueueRequest.state.in_(_ACTIVE),
            )
            .order_by(VideoQueueRequest.enqueued_at, VideoQueueRequest.id)
            .limit(1),
        )
        if active is not None:
            return active, lane
        queued = await db.scalar(
            select(VideoQueueRequest)
            .where(
                VideoQueueRequest.lane_id == lane_id,
                VideoQueueRequest.state.in_(("queued", "rate_limited")),
            )
            .order_by(VideoQueueRequest.enqueued_at, VideoQueueRequest.id)
            .limit(1),
        )
        return (queued, lane) if queued is not None else None


async def _claim(row: VideoQueueRequest) -> bool:
    async with SESSION_LOCAL() as db:
        lane = await db.get(VideoQueueLane, row.lane_id, with_for_update=True)
        if lane is None or (lane.not_before and lane.not_before > utc_now()):
            return False
        active = await db.scalar(
            select(VideoQueueRequest.id)
            .where(
                VideoQueueRequest.lane_id == lane.id,
                VideoQueueRequest.state.in_(_ACTIVE),
            )
            .limit(1),
        )
        first = await db.scalar(
            select(VideoQueueRequest.id)
            .where(
                VideoQueueRequest.lane_id == lane.id,
                VideoQueueRequest.state.in_(("queued", "rate_limited")),
            )
            .order_by(VideoQueueRequest.enqueued_at, VideoQueueRequest.id)
            .limit(1),
        )
        current = await db.get(VideoQueueRequest, row.id, with_for_update=True)
        if (
            active is not None
            or first != row.id
            or current is None
            or current.lane_id != lane.id
            or current.state not in ("queued", "rate_limited")
        ):
            return False
        current.state, current.error_message = "claimed", None
        current.revision += 1
        await db.commit()
        return True


async def _cooldown(row: VideoQueueRequest, exc: Exception) -> None:
    delay = getattr(exc, "retry_after_seconds", None)
    if delay is None:
        delay = min(300, 30 * 2 ** min(row.rate_limit_count, 4))
    retry_at = utc_now() + timedelta(seconds=max(1.0, float(delay)))
    async with SESSION_LOCAL() as db:
        lane = await db.get(VideoQueueLane, row.lane_id, with_for_update=True)
        current = await db.get(VideoQueueRequest, row.id, with_for_update=True)
        if lane is None or current is None or current.state != "submitting":
            return
        lane.not_before = max(lane.not_before or retry_at, retry_at)
        current.state = "cancelled" if current.cancel_requested else "rate_limited"
        current.rate_limit_count += 1
        current.error_reason, current.error_status_code = "rate_limit", 429
        current.error_message = _diagnostic(row.token, classify_api_error(exc).message)
        current.revision += 1
        await db.commit()
    logger.warning("video queue rate limited", extra={"request_id": row.token, "next_retry_at": retry_at.isoformat()})


class _RemoteCancelled(Exception):
    pass


async def _confirm_cancelled(request_id: str, task_id: str) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VideoQueueRequest).where(VideoQueueRequest.token == request_id).with_for_update())
        if row is None or row.state not in _ACTIVE or row.provider_task_id != task_id:
            return
        row.state, row.request_json, row.result_json = "cancelled", None, None
        row.error_reason, row.error_status_code, row.error_message = None, None, None
        if row.user_id is None or row.entity_id is None:
            row.consumed_at = utc_now()
        row.revision += 1
        row.updated_at = utc_now()
        await db.commit()


async def _poll(row: VideoQueueRequest, config: ProviderConfig) -> None:
    provider = build_provider(config, VideoGenProvider)

    async def before_poll() -> None:
        current, _lane = await _read(row.token)
        if current.state not in _ACTIVE or current.provider_task_id != row.provider_task_id:
            raise _RemoteCancelled
        if await _readiness(current, before_submit=False) == "gone" and not current.cancel_requested:
            await cancel_video_attempt(row.token)
            current, _lane = await _read(row.token)
        if current.cancel_requested:
            try:
                async with asyncio.timeout(15):
                    outcome = await provider.cancel(current.provider_task_id or "")
            except Exception:
                logger.warning("video queue cancellation awaits retry", extra={"request_id": row.token})
            else:
                if outcome == "cancelled":
                    await _confirm_cancelled(row.token, current.provider_task_id or "")
                    raise _RemoteCancelled

    try:
        status = await poll_video_task(provider, row.provider_task_id or "", before_poll=before_poll)
    except _RemoteCancelled:
        return
    except Exception as exc:
        await _block(row.token, classify_api_error(exc).message)
        return
    await _finish(row.token, status)


async def _run_lane(lane_id: int) -> None:
    selected = await _select_lane_request(lane_id)
    if selected is None:
        return
    row, lane = selected
    try:
        if row.state in _ACTIVE:
            if not row.provider_task_id:
                if row.state == "submitting":
                    await _block(row.token, "视频提交结果尚未确认，等待核对原任务")
                return
            config = await _config(row, lane)
            if config is None:
                if row.state != "blocked":
                    await _block(row.token, "原视频凭据不可用，等待恢复原凭据或核对任务")
                return
            _CONFIGS[row.token] = config
            if row.state == "blocked":
                if not await _change(row.token, expected=("blocked",), state="processing", error_message=None):
                    return
                row, _lane = await _read(row.token)
            await _poll(row, config)
            return
        readiness = await _readiness(row)
        if readiness == "pending":
            # 入队与业务引用落盘之间保留原队首，后到请求不能趁此窗口超前。
            return
        if readiness == "gone":
            await cancel_video_attempt(row.token)
            return
        if readiness == "paused":
            await _change(row.token, expected=_PENDING, state="paused")
            return
        config = await _config(row, lane)
        if config is None:
            await _change(
                row.token,
                expected=_PENDING,
                state="failed",
                error_reason="format_error",
                error_message="视频服务配置已变更，原请求无法提交",
                request_json=None,
            )
            return
        target_lane = await _lane_id(config)
        if target_lane != row.lane_id:
            await _change(
                row.token,
                expected=_PENDING,
                lane_id=target_lane,
                state="queued",
                enqueued_at=utc_now(),
                rate_limit_count=0,
                error_message=None,
            )
            return
        if not await _claim(row):
            return
        _CONFIGS[row.token] = config
        # 认领后重查取消、维护和业务代次，再进行唯一一次非幂等提交。
        current, _lane = await _read(row.token)
        readiness = await _readiness(current)
        if readiness == "pending":
            await _change(row.token, expected=("claimed",), state="queued")
            return
        if current.cancel_requested or readiness == "gone":
            await _change(row.token, expected=("claimed",), state="cancelled", request_json=None)
            return
        if current.pause_requested or readiness == "paused":
            await _change(row.token, expected=("claimed",), state="paused")
            return
        request = _REQUEST.validate_json(current.request_json or "")
        async with SESSION_LOCAL() as db:
            claimed = await db.get(VideoQueueRequest, row.id, with_for_update=True)
            if claimed is None or claimed.state != "claimed":
                return
            if claimed.cancel_requested or claimed.pause_requested:
                claimed.state = "cancelled" if claimed.cancel_requested else "paused"
                claimed.revision += 1
                await db.commit()
                return
            claimed.state = "submitting"
            claimed.revision += 1
            await db.commit()

        async def submit(provider: VideoGenProvider) -> VideoJobStatus:
            status = await provider.submit(request)
            if not status.task_id:
                raise ProviderResultUnknownError("POST", config.base_url)
            return status

        try:
            status = await execute_with_fallback([config], VideoGenProvider, submit, user_id=row.user_id)
        except Exception as exc:
            classified = classify_api_error(exc)
            if classified.reason == FailoverReason.result_unknown or getattr(exc, "result_unknown", False):
                await _block(row.token, classified.message)
            elif classified.reason == FailoverReason.rate_limit:
                await _cooldown(row, exc)
            else:
                await _change(
                    row.token,
                    expected=("submitting",),
                    state="failed",
                    error_reason=classified.reason.value,
                    error_status_code=classified.status_code,
                    error_message=_diagnostic(row.token, classified.message),
                    request_json=None,
                )
            logger.warning(
                "video queue submit interrupted",
                extra={
                    "queue_request_id": row.token,
                    "provider": config.provider_name,
                    "model": config.model,
                    "reason": classified.reason.value,
                    "status_code": classified.status_code,
                    "error": _diagnostic(row.token, classified.message),
                },
            )
            return
        while True:
            try:
                await _change(
                    row.token,
                    expected=("submitting",),
                    state="processing",
                    provider_task_id=status.task_id,
                    request_json=None,
                    error_message=None,
                )
                break
            except SQLAlchemyError:
                logger.warning("video queue accepted handle awaits storage", extra={"queue_request_id": row.token})
                await asyncio.sleep(1)
        current, _lane = await _read(row.token)
        await _poll(current, config)
    except GenerationWorkPaused:
        await _change(row.token, expected=_PENDING + ("claimed",), state="paused")
    except SQLAlchemyError:
        # 前置数据库读取失败不代表业务已删除，也不代表供应商拒单。
        current, _lane = await _read(row.token)
        if current.state == "claimed":
            await _change(row.token, expected=("claimed",), state="queued")
        logger.warning("video queue database operation awaits retry", extra={"queue_request_id": row.token})
    except asyncio.CancelledError:
        current, _lane = await _read(row.token)
        if current.state == "claimed":
            await _change(row.token, expected=("claimed",), state="queued")
        elif current.state == "submitting":
            await _block(row.token, "视频提交被中断，等待核对原任务")
        raise
    except Exception as exc:
        current, _lane = await _read(row.token)
        if current.state in ("submitting", "processing", "blocked"):
            await _block(row.token, classify_api_error(exc).message)
        else:
            await _change(
                row.token,
                expected=_PENDING + ("claimed",),
                state="stopped",
                request_json=None,
                error_message=_diagnostic(row.token, classify_api_error(exc).message),
            )
        logger.warning(
            "video queue worker interrupted",
            extra={
                "queue_request_id": row.token,
                "error": _diagnostic(row.token, classify_api_error(exc).message),
            },
        )
    finally:
        current, _lane = await _read(row.token)
        if current.state not in _ACTIVE:
            _CONFIGS.pop(row.token, None)


async def _resume_paused() -> None:
    async with SESSION_LOCAL() as db:
        rows = list(await db.scalars(select(VideoQueueRequest).where(VideoQueueRequest.state == "paused")))
    for row in rows:
        try:
            readiness = await _readiness(row)
        except Exception:
            logger.warning(
                "video queue owner inspection awaits retry",
                extra={"queue_request_id": row.token},
                exc_info=True,
            )
            continue
        if readiness == "ready":
            await _change(row.token, expected=("paused",), state="queued", pause_requested=False, enqueued_at=utc_now())
        elif readiness == "gone":
            await cancel_video_attempt(row.token)


async def _dispatch() -> None:
    global _STOPPING
    while not _STOPPING:
        if _LEADER is not None:
            try:
                await _LEADER.execute(text("SELECT 1"))
            except Exception:
                _STOPPING = True
                await _WORK.drain()
                raise RuntimeError("Video queue dispatcher lost its database leadership") from None
        try:
            await _resume_paused()
            async with SESSION_LOCAL() as db:
                lane_ids = list(
                    await db.scalars(
                        select(VideoQueueRequest.lane_id)
                        .where(
                            VideoQueueRequest.state.in_(_ACTIVE + ("queued", "rate_limited")),
                        )
                        .distinct(),
                    ),
                )
            for lane_id in lane_ids:
                existing = _LANES.get(lane_id)
                if existing is not None and not existing.done():
                    continue
                task = asyncio.create_task(_run_lane(lane_id), name=f"video-queue-{lane_id}")
                _LANES[lane_id] = task
                task.add_done_callback(
                    lambda done, key=lane_id: _LANES.pop(key, None) if _LANES.get(key) is done else None,
                )
                _WORK.add(task, on_error=_worker_error)
        except Exception:
            logger.exception("video queue dispatcher awaits retry")
        await asyncio.sleep(0.5)


def _worker_error(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("video queue task failed", exc_info=task.exception())


async def prepare_video_queue() -> None:
    """恢复持久队列；远端占用由独立任务继续收尾。"""
    global _STOPPING, _LEADER
    _STOPPING = False
    connection = await ENGINE.connect()
    connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
    try:
        if not await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LEADER_KEY}):
            raise RuntimeError("视频队列已由另一后端进程运行，请保持单实例部署")
    except BaseException:
        await connection.close()
        raise
    try:
        async with SESSION_LOCAL() as db:
            await db.execute(
                update(VideoQueueRequest)
                .where(VideoQueueRequest.state == "claimed")
                .values(
                    state="queued",
                    revision=VideoQueueRequest.revision + 1,
                ),
            )
            await db.execute(
                update(VideoQueueRequest)
                .where(VideoQueueRequest.state == "submitting")
                .values(
                    state="blocked",
                    error_reason="result_unknown",
                    error_message="视频提交中断，等待核对原任务",
                    revision=VideoQueueRequest.revision + 1,
                ),
            )
            await db.commit()
        await cleanup_video_queue()
    except BaseException:
        await _close_leader(connection)
        raise
    _LEADER = connection


async def _close_leader(connection: AsyncConnection) -> None:
    try:
        # AUTOCOMMIT 仍有 SQLAlchemy 事务状态；取消探活后先清除失效事务，失联会话的锁随连接关闭释放。
        await connection.rollback()
        if not connection.invalidated:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LEADER_KEY})
    except BaseException:
        await connection.invalidate()
        raise
    finally:
        await connection.close()


async def start_video_queue() -> None:
    _DISPATCHER.start(_dispatch())


async def stop_video_queue() -> None:
    global _STOPPING
    _STOPPING = True
    await _DISPATCHER.stop()


async def drain_video_queue() -> None:
    global _LEADER
    try:
        await _WORK.drain()
    finally:
        _LANES.clear()
        _CONFIGS.clear()
        connection, _LEADER = _LEADER, None
        if connection is not None:
            await _close_leader(connection)


async def resume_user_video_queue(user_id: int) -> None:
    await _resume_paused()


async def cleanup_video_queue() -> None:
    async with SESSION_LOCAL() as db:
        rows = list(
            await db.scalars(
                select(VideoQueueRequest).where(
                    VideoQueueRequest.state.in_(_TERMINAL),
                    (VideoQueueRequest.kind == "action")
                    | (VideoQueueRequest.consumed_at < utc_now() - timedelta(days=7)),
                ),
            ),
        )
    for row in rows:
        if await _readiness(row, before_submit=False) != "gone":
            continue
        async with SESSION_LOCAL() as db:
            await db.execute(
                delete(VideoQueueRequest).where(
                    VideoQueueRequest.id == row.id,
                    VideoQueueRequest.revision == row.revision,
                    VideoQueueRequest.state.in_(_TERMINAL),
                ),
            )
            await db.commit()
