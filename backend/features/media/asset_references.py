from collections.abc import Collection, Sequence
from datetime import timedelta

from infrastructure.assets.references import asset_paths
from infrastructure.models import id_in
from infrastructure.text import utc_now
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.references import scanned_asset_paths
from features.assets.service import asset_paths_in_session
from features.configuration.settings.values import get_user_setting

from .models import ImageGenJob, MediaManualOperation, VideoGenJob
from .voice_models import CustomVoice
from .voice_queries import custom_voice_tokens
from .voice_usage import used_voice_ids


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced = await scanned_asset_paths(
        db,
        user_id,
        select(MediaManualOperation.snapshot_json, MediaManualOperation.result_json).where(
            MediaManualOperation.user_id == user_id, MediaManualOperation.created_at >= utc_now() - timedelta(days=90)
        ),
    )
    referenced |= await scanned_asset_paths(
        db,
        user_id,
        select(ImageGenJob.result_json).where(
            ImageGenJob.user_id == user_id, ImageGenJob.cleanup_requested_at.is_(None)
        ),
    )
    referenced |= await scanned_asset_paths(
        db,
        user_id,
        select(VideoGenJob.review_result_json).where(
            VideoGenJob.user_id == user_id,
            VideoGenJob.cleanup_requested_at.is_(None),
            VideoGenJob.state.in_(("queued", "running", "paused", "unknown", "succeeded")),
            or_(VideoGenJob.conversation_id.is_not(None), VideoGenJob.checkpoint.is_not(None)),
        ),
    )
    selected_tokens = custom_voice_tokens(await get_user_setting(db, user_id, "companion.voice_id"))
    rows = list(
        await db.execute(
            select(
                CustomVoice.id,
                CustomVoice.token,
                CustomVoice.is_current,
                CustomVoice.expires_at,
                CustomVoice.reference_asset_id,
                CustomVoice.preview_asset_id,
            ).where(CustomVoice.user_id == user_id)
        )
    )
    used = await used_voice_ids(db, user_id, [row.id for row in rows])
    now = utc_now()
    ids = {
        asset_id
        for row in rows
        if row.id in used or row.is_current or row.token in selected_tokens or (row.expires_at and row.expires_at > now)
        for asset_id in (row.reference_asset_id, row.preview_asset_id)
        if asset_id is not None
    }
    referenced.update((await asset_paths_in_session(db, user_id, ids)).values())
    return referenced


async def pending_video_delivery_asset_paths(db: AsyncSession, user_id: int, job_ids: Collection[int]) -> set[str]:
    """等待业务采纳的已通过视频和检查依据沿原任务保留，独立于任务诊断保留期。"""
    if not job_ids:
        return set()
    return await scanned_asset_paths(
        db,
        user_id,
        select(VideoGenJob.video_url, VideoGenJob.review_result_json).where(
            VideoGenJob.user_id == user_id,
            id_in(VideoGenJob.id, job_ids),
            VideoGenJob.conversation_id.is_(None),
            VideoGenJob.cleanup_requested_at.is_(None),
            VideoGenJob.state == "succeeded",
        ),
    )


async def cleanup_obsolete_assets(db: AsyncSession, user_id: int) -> set[str]:
    released: set[str] = set()
    expired = list(
        await db.scalars(
            select(CustomVoice)
            .where(CustomVoice.user_id == user_id, CustomVoice.version == 0, CustomVoice.expires_at <= utc_now())
            .order_by(CustomVoice.id)
            .limit(200)
            .with_for_update(skip_locked=True)
        )
    )
    used = await used_voice_ids(db, user_id, [row.id for row in expired])
    for row in expired:
        if row.id in used:
            continue
        released |= asset_paths([row.reference_path, row.preview_path], user_id)
        await db.delete(row)
    return released


async def forget_deleted_assets(db: AsyncSession, user_id: int, paths: Sequence[str]) -> None:
    if not paths:
        return
    await db.execute(
        update(CustomVoice)
        .where(CustomVoice.user_id == user_id, CustomVoice.reference_path.in_(paths))
        .values(reference_path=None, reference_asset_id=None)
    )
    await db.execute(
        update(CustomVoice)
        .where(CustomVoice.user_id == user_id, CustomVoice.preview_path.in_(paths))
        .values(preview_path="", preview_asset_id=None)
    )
