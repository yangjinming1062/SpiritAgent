"""已发布版本和制作任务的显式资产依赖与保留期回收。"""

from datetime import timedelta

from infrastructure.assets.references import asset_paths
from infrastructure.text import utc_now
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.service import asset_paths_in_session
from features.jobs.service import cleanup_job_assets, job_asset_paths

from .models import VisualItem, VisualJob, VisualPlayback, VisualPresentation, VisualVersion, VisualVersionAsset


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    ids = list(await db.scalars(select(VisualVersionAsset.asset_id).where(VisualVersionAsset.user_id == user_id)))
    paths = set((await asset_paths_in_session(db, user_id, ids)).values())
    paths |= await job_asset_paths(db, user_id)
    for pending, current in await db.execute(
        select(VisualPresentation.pending_static_json, VisualPresentation.current_static_json).where(
            VisualPresentation.user_id == user_id
        )
    ):
        paths |= asset_paths([pending, current], user_id)
    # 尚未交接的供应商候选仍由工作项保护；发布后依赖由版本关联表持有。
    for row in await db.scalars(
        select(VisualJob).where(
            VisualJob.user_id == user_id,
            or_(
                VisualJob.cleanup_pending.is_(True),
                VisualJob.finished_at.is_(None),
                VisualJob.finished_at >= utc_now() - timedelta(days=7),
            ),
        )
    ):
        paths |= asset_paths([row.snapshot_json, row.checkpoint, row.candidates_json], user_id)
    return paths


async def cleanup_obsolete_assets(db: AsyncSession, user_id: int) -> set[str]:
    released = await cleanup_job_assets(db, user_id)
    archived = (
        select(VisualJob.id)
        .select_from(VisualJob)
        .where(
            VisualJob.user_id == user_id,
            VisualJob.state.in_(("succeeded", "failed", "cancelled")),
            VisualJob.cleanup_pending.is_(False),
            VisualJob.finished_at < utc_now() - timedelta(days=90),
        )
    )
    await db.execute(
        update(VisualJob.__table__)
        .where(VisualJob.id.in_(archived))
        .values(
            snapshot_json={},
            candidates_json=None,
            feedback="",
            video_feedback="",
        )
    )
    cutoff = utc_now() - timedelta(days=7)
    await db.execute(
        delete(VisualPlayback).where(
            VisualPlayback.user_id == user_id,
            VisualPlayback.status.not_in(("queued", "claimed", "started")),
            VisualPlayback.updated_at < cutoff,
        )
    )
    unused = select(VisualVersion.id).where(
        VisualVersion.user_id == user_id,
        VisualVersion.created_at < cutoff,
        ~select(VisualItem.id).where(VisualItem.current_version_id == VisualVersion.id).exists(),
        ~select(VisualPresentation.user_id)
        .where(
            or_(
                VisualPresentation.current_version_id == VisualVersion.id,
                VisualPresentation.loop_version_id == VisualVersion.id,
            )
        )
        .exists(),
        ~select(VisualPlayback.id).where(VisualPlayback.version_id == VisualVersion.id).exists(),
        ~select(VisualJob.id)
        .where(
            VisualJob.state.in_(("queued", "running", "paused", "unknown")),
            or_(VisualJob.result_version_id == VisualVersion.id, VisualJob.result_image_version_id == VisualVersion.id),
        )
        .exists(),
    )
    versions = list(await db.scalars(unused))
    if not versions:
        return released
    asset_ids = list(
        await db.scalars(select(VisualVersionAsset.asset_id).where(VisualVersionAsset.version_id.in_(versions)))
    )
    paths = set((await asset_paths_in_session(db, user_id, asset_ids)).values())
    await db.execute(delete(VisualVersion).where(VisualVersion.id.in_(versions)))
    return paths | released
