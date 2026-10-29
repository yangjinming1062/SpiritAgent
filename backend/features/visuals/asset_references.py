"""已发布版本和制作任务的显式资产依赖与保留期回收。"""

import json
from datetime import timedelta

from infrastructure.assets.references import asset_paths
from infrastructure.text import utc_now
from sqlalchemy import delete, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from features.assets.service import asset_paths_in_session
from features.jobs.service import cleanup_job_assets, job_asset_paths

from .eligibility import automatic_media_ready
from .models import VisualItem, VisualJob, VisualPlayback, VisualPresentation, VisualVersion, VisualVersionAsset
from .schemas import StageComposition, VisualSnapshot


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    ids = list(await db.scalars(select(VisualVersionAsset.asset_id).where(VisualVersionAsset.user_id == user_id)))
    paths = set((await asset_paths_in_session(db, user_id, ids)).values())
    paths |= await job_asset_paths(db, user_id)
    for pending, current, composition in await db.execute(
        select(
            VisualPresentation.pending_static_json,
            VisualPresentation.current_static_json,
            VisualPresentation.composition_json,
        ).where(VisualPresentation.user_id == user_id)
    ):
        paths |= asset_paths([pending, current, composition], user_id)
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


async def _automatic_version_ids(db: AsyncSession, user_id: int) -> set[int]:
    """每份冻结身份、造型、动作与画幅保留最新合格实现，供目录回退与准备复用。"""

    rows = await db.stream(
        select(VisualVersion, VisualItem.asset_kind)
        .join(VisualItem, VisualItem.id == VisualVersion.visual_id)
        .where(
            VisualItem.user_id == user_id,
            VisualItem.status == "ready",
            VisualItem.media_kind == "video",
            VisualItem.asset_kind.in_(("actor", "cinematic")),
            VisualVersion.snapshot_json["source"]["media_semantics"]["auto_selectable"].as_boolean().is_(True),
        )
        .order_by(VisualVersion.id.desc()),
        execution_options={"yield_per": 32},
    )
    protected: set[int] = set()
    selected: set[str] = set()
    try:
        async for version, asset_kind in rows:
            source = VisualSnapshot.model_validate(version.snapshot_json).source
            if source.kind not in ("actor", "cinematic"):
                continue
            key = json.dumps(
                [
                    version.visual_id,
                    source.identity_hash,
                    source.portrait_hash,
                    source.identity_reference_role,
                    source.outfit.content_hash,
                    source.action.media_basis(),
                    version.width,
                    version.height,
                ],
                sort_keys=True,
            )
            if key in selected or not automatic_media_ready(
                media_kind="video",
                asset_kind=asset_kind,
                media_path=version.media_path,
                auto_selectable=source.media_semantics.auto_selectable if source.media_semantics else None,
                clip_kind=source.action.clip_kind,
                body=version.body_json,
            ):
                continue
            selected.add(key)
            protected.add(version.id)
    finally:
        await rows.close()
    return protected


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
    dependent_version = aliased(VisualVersion)
    protected: set[tuple[int, int]] = set()
    for raw in await db.scalars(
        select(VisualPresentation.composition_json).where(VisualPresentation.user_id == user_id)
    ):
        if not raw:
            continue
        composition = StageComposition.model_validate(raw)
        for ref in (composition.actor, composition.cinematic):
            if ref:
                protected.add((ref.visual_id, ref.version))
        if composition.scene_visual_id is not None and composition.scene_version is not None:
            protected.add((composition.scene_visual_id, composition.scene_version))
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
        ~select(dependent_version.id).where(dependent_version.input_image_version_id == VisualVersion.id).exists(),
        ~select(VisualJob.id)
        .where(
            VisualJob.state.in_(("queued", "running", "paused", "unknown")),
            or_(VisualJob.result_version_id == VisualVersion.id, VisualJob.result_image_version_id == VisualVersion.id),
        )
        .exists(),
    )
    if protected:
        unused = unused.where(tuple_(VisualVersion.visual_id, VisualVersion.number).not_in(protected))
    automatic = await _automatic_version_ids(db, user_id)
    if automatic:
        unused = unused.where(VisualVersion.id.not_in(automatic))
    versions = list(await db.scalars(unused))
    if not versions:
        return released
    asset_ids = list(
        await db.scalars(select(VisualVersionAsset.asset_id).where(VisualVersionAsset.version_id.in_(versions)))
    )
    paths = set((await asset_paths_in_session(db, user_id, asset_ids)).values())
    await db.execute(delete(VisualVersion).where(VisualVersion.id.in_(versions)))
    return paths | released
