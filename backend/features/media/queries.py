"""制作任务查询、消息绑定及失效操作；事务归调用方。"""

from collections.abc import Collection

from infrastructure.assets import asset_store
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.text import utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .chain import MediaChainState
from .contracts import ReleasedVideoJobs, VideoJobAssetReferences, VideoJobSnapshot
from .identity_contracts import VisualIdentitySnapshot
from .models import VideoGenJob
from .reference_reader import visual_identity_is_current
from .review_contracts import VisualReview


def video_job_snapshot(row: VideoGenJob) -> VideoJobSnapshot:
    state = MediaChainState.model_validate(row.checkpoint or {})
    path = row.video_url or row.candidate_video_url
    parsed = parse_companion_asset_path(path)
    available = (
        parsed is not None
        and parsed[0] == row.user_id
        and asset_store.resolve_companion_asset_path(*parsed) is not None
    )
    candidate_ready = False
    if available and row.state not in ("failed", "cancelled"):
        candidate_ready = row.state == "succeeded" or any(
            c.review and c.review.verdict == "pass" and c.path == path for c in state.candidates
        )
    return VideoJobSnapshot(
        id=row.id,
        user_id=row.user_id,
        revision=row.revision,
        session_id=str(row.conversation_id) if row.conversation_id is not None else None,
        source_message_id=row.source_message_id,
        request_scope=row.params_json.get("request_scope"),
        structured_reply=row.structured_reply,
        media_id=row.media_id,
        reply_message_id=row.reply_message_id,
        status={"queued": "retry_pending", "unknown": "result_unknown", "cancelled": "discarded"}.get(
            row.state, row.stage if row.state in {"running", "paused"} else row.state
        ),
        video_url=row.video_url,
        candidate_video_url=row.candidate_video_url,
        error_reason=row.error_reason,
        error_message=row.error,
        cleanup_requested_at=row.cleanup_requested_at,
        candidate_ready=candidate_ready,
        review=VisualReview.model_validate(row.review_result_json) if row.review_result_json else None,
        video_queue=state.video_queue if row.state not in ("succeeded", "failed", "cancelled") else None,
    )


async def get_job(db: AsyncSession, job_id: int, user_id: int) -> VideoJobSnapshot | None:
    row = await db.scalar(select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id))
    return video_job_snapshot(row) if row is not None else None


async def get_video_jobs(db: AsyncSession, user_id: int, job_ids: Collection[int]) -> dict[int, VideoJobSnapshot]:
    if not job_ids:
        return {}
    rows = await db.scalars(select(VideoGenJob).where(VideoGenJob.id.in_(job_ids), VideoGenJob.user_id == user_id))
    return {row.id: video_job_snapshot(row) for row in rows}


async def pending_conversation_video_jobs(
    db: AsyncSession, user_id: int, session_id: str, request_scope: str | None = None
) -> tuple[VideoJobSnapshot, ...]:
    statement = select(VideoGenJob).where(
        VideoGenJob.user_id == user_id,
        VideoGenJob.conversation_id == int(session_id),
        VideoGenJob.cleanup_requested_at.is_(None),
    )
    if request_scope is not None:
        statement = statement.where(VideoGenJob.params_json["request_scope"].astext == request_scope)
    else:
        statement = statement.where(~VideoGenJob.state.in_(("succeeded", "failed", "unknown", "cancelled")))
    return tuple(video_job_snapshot(row) for row in await db.scalars(statement.order_by(VideoGenJob.id)))


async def bind_video_reply(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: str,
    job_id: int,
    media_id: str,
    message_id: int,
) -> VideoJobSnapshot:
    row = await db.scalar(select(VideoGenJob).where(VideoGenJob.id == job_id).with_for_update())
    if row is None or row.user_id != user_id or row.conversation_id != int(session_id) or row.media_id != media_id:
        raise ValueError("Video task does not belong to this reply")
    if row.reply_message_id not in (None, message_id) and row.state not in ("succeeded", "failed", "unknown"):
        raise ValueError("Pending video already belongs to another reply")
    frozen_identity = row.params_json.get("identity_snapshot")
    if frozen_identity and not await visual_identity_is_current(
        db, user_id, VisualIdentitySnapshot.model_validate(frozen_identity)
    ):
        row.state, row.stage, row.error_reason, row.error = (
            "paused",
            "confirmation",
            "identity_changed",
            "原任务身份已失效，候选已保留，不能按当前身份交付",
        )
    if row.reply_message_id is None and row.structured_reply:
        row.reply_message_id = message_id
    emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
    await db.flush()
    return video_job_snapshot(row)


async def transfer_reply_videos(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: str,
    from_message_id: int,
    to_message_id: int,
) -> tuple[VideoJobSnapshot, ...]:
    """替换回答时先锁视频再锁消息；交接与消息替换由调用方同事务提交。"""
    rows = await db.scalars(
        select(VideoGenJob)
        .where(VideoGenJob.reply_message_id == from_message_id)
        .order_by(VideoGenJob.id)
        .with_for_update(),
    )
    transferred = []
    for row in rows:
        if row.user_id != user_id or row.conversation_id != int(session_id) or not row.structured_reply:
            raise ValueError("Video task does not belong to this reply")
        row.reply_message_id = to_message_id
        await db.flush()
        transferred.append(video_job_snapshot(row))
    return tuple(transferred)


def _asset_references(row: VideoGenJob) -> VideoJobAssetReferences:
    return VideoJobAssetReferences(
        video_url=row.video_url,
        candidate_video_url=row.candidate_video_url,
        params_json=row.params_json,
        generation_state_json=row.checkpoint or {},
    )


async def invalidate_conversation_video_jobs(
    db: AsyncSession,
    user_id: int,
    message_ids: Collection[int],
    session_ids: Collection[str] = (),
) -> ReleasedVideoJobs:
    """源用户消息先由会话所有者加锁；视频行依 ID 锁定，与终态交付保持相同顺序。"""
    ownership = or_(VideoGenJob.source_message_id.in_(message_ids), VideoGenJob.reply_message_id.in_(message_ids))
    if session_ids:
        ownership = or_(ownership, VideoGenJob.conversation_id.in_([int(value) for value in session_ids]))
    rows = list(
        await db.scalars(
            select(VideoGenJob)
            .where(VideoGenJob.user_id == user_id, ownership)
            .order_by(VideoGenJob.id)
            .with_for_update(),
        ),
    )
    for row in rows:
        row.cleanup_requested_at = utc_now()
        row.cleanup_pending = True
    return ReleasedVideoJobs(
        job_ids=frozenset(row.id for row in rows),
        references=tuple(_asset_references(row) for row in rows),
    )
