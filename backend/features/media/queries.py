"""制作任务查询、消息绑定及失效操作；事务归调用方。"""

from collections.abc import Collection

from infrastructure.assets import asset_store
from infrastructure.text import utc_now
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .chain import MediaChainState
from .contracts import ReleasedVideoJobs, VideoJobAssetReferences, VideoJobSnapshot
from .models import VideoGenJob


def video_job_snapshot(row: VideoGenJob) -> VideoJobSnapshot:
    path = row.video_url or row.candidate_video_url
    parsed = asset_store.parse_companion_asset_path(path)
    available = (
        parsed is not None
        and parsed[0] == row.user_id
        and asset_store.resolve_companion_asset_path(*parsed) is not None
    )
    candidate_ready = False
    if available and row.status not in ("failed", "discarded"):
        candidate_ready = (
            row.status == "succeeded"
            or MediaChainState.model_validate_json(row.generation_state_json).source_path == path
        )
    return VideoJobSnapshot(
        id=row.id,
        user_id=row.user_id,
        session_id=row.session_id,
        source_message_id=row.source_message_id,
        structured_reply=row.structured_reply,
        media_id=row.media_id,
        reply_message_id=row.reply_message_id,
        status=row.status,
        video_url=row.video_url,
        candidate_video_url=row.candidate_video_url,
        error_reason=row.error_reason,
        error_message=row.error_message,
        cleanup_requested_at=row.cleanup_requested_at,
        candidate_ready=candidate_ready,
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
    db: AsyncSession,
    user_id: int,
    session_id: str,
) -> tuple[VideoJobSnapshot, ...]:
    rows = await db.scalars(
        select(VideoGenJob).where(
            VideoGenJob.user_id == user_id,
            VideoGenJob.session_id == session_id,
            VideoGenJob.media_id.is_not(None),
            VideoGenJob.cleanup_requested_at.is_(None),
            VideoGenJob.status.not_in(("succeeded", "failed", "result_unknown", "discarded")),
        ),
    )
    return tuple(video_job_snapshot(row) for row in rows)


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
    if row is None or row.user_id != user_id or row.session_id != session_id or row.media_id != media_id:
        raise ValueError("Video task does not belong to this reply")
    if row.reply_message_id not in (None, message_id) and row.status not in {"succeeded", "failed", "result_unknown"}:
        raise ValueError("Pending video already belongs to another reply")
    if row.reply_message_id is None and row.structured_reply:
        row.reply_message_id = message_id
    return video_job_snapshot(row)


def _asset_references(row: VideoGenJob) -> VideoJobAssetReferences:
    return VideoJobAssetReferences(
        video_url=row.video_url,
        candidate_video_url=row.candidate_video_url,
        params_json=row.params_json,
        generation_state_json=row.generation_state_json,
    )


async def live_video_asset_references(db: AsyncSession, user_id: int) -> tuple[VideoJobAssetReferences, ...]:
    rows = await db.scalars(
        select(VideoGenJob).where(
            VideoGenJob.user_id == user_id,
            VideoGenJob.cleanup_requested_at.is_(None),
            VideoGenJob.status.not_in(("succeeded", "discarded", "cancelled", "failed")),
        ),
    )
    return tuple(_asset_references(row) for row in rows)


async def invalidate_conversation_video_jobs(
    db: AsyncSession,
    user_id: int,
    message_ids: Collection[int],
    session_ids: Collection[str] = (),
) -> ReleasedVideoJobs:
    """源用户消息先由会话所有者加锁；视频行依 ID 锁定，与终态交付保持相同顺序。"""
    ownership = or_(VideoGenJob.source_message_id.in_(message_ids), VideoGenJob.reply_message_id.in_(message_ids))
    if session_ids:
        ownership = or_(ownership, VideoGenJob.session_id.in_(session_ids))
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
    return ReleasedVideoJobs(
        job_ids=frozenset(row.id for row in rows),
        references=tuple(_asset_references(row) for row in rows),
    )
