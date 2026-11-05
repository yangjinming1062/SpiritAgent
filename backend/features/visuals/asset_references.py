"""已发布版本和制作任务的显式资产依赖与保留期回收。"""

import json
from datetime import timedelta

from infrastructure.text import utc_now
from sqlalchemy import delete, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from features.assets.references import scanned_asset_paths
from features.assets.service import asset_paths_for_ids

from .eligibility import automatic_media_ready
from .models import (
    VisualItem,
    VisualJob,
    VisualPlayback,
    VisualPresentation,
    VisualVersion,
    VisualVersionAsset,
)
from .schemas import StageComposition, VisualSnapshot


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    ids = list(await db.scalars(select(VisualVersionAsset.asset_id).where(VisualVersionAsset.user_id == user_id)))
    paths = await asset_paths_for_ids(db, user_id, ids)
    paths |= await scanned_asset_paths(
        db,
        user_id,
        select(
            VisualPresentation.pending_static_json,
            VisualPresentation.current_static_json,
            VisualPresentation.composition_json,
        ).where(VisualPresentation.user_id == user_id),
    )
    # 制作候选和长期导入资料由 JobAsset 持有；静态冻结素材没有版本关系，仍扫描呈现快照。
    return paths


async def _automatic_version_ids(db: AsyncSession, user_id: int) -> set[int]:
    """每组冻结素材与画幅保留最新合格版本，供目录回退与准备复用。"""

    rows = await db.stream(
        select(VisualVersion, VisualItem.media_kind)
        .join(VisualItem, VisualItem.id == VisualVersion.visual_id)
        .where(
            VisualItem.user_id == user_id,
            VisualItem.status == "ready",
            VisualVersion.snapshot_json["source"]["media_semantics"]["auto_selectable"].as_boolean().is_(True),
        )
        .order_by(VisualVersion.id.desc()),
        execution_options={"yield_per": 32},
    )
    protected: set[int] = set()
    selected: set[str] = set()
    try:
        async for version, media_kind in rows:
            snapshot = VisualSnapshot.model_validate(version.snapshot_json)
            source = snapshot.source
            if (
                snapshot.kind not in ("actor", "cinematic")
                or source.kind not in ("actor", "cinematic", "analysis")
                or source.outfit is None
                or (media_kind == "video" and source.action is None)
            ):
                continue
            scene = getattr(source, "scene", None)
            clip_kind = (
                None
                if media_kind == "image"
                else source.clip_kind
                if source.kind == "analysis"
                else source.action.clip_kind
            )
            key = json.dumps(
                [
                    version.visual_id,
                    source.identity_hash,
                    source.portrait_hash,
                    source.identity_reference_role,
                    source.outfit.content_hash,
                    source.action.media_basis() if source.action else None,
                    scene.content_hash if scene else None,
                    clip_kind,
                    version.width,
                    version.height,
                ],
                sort_keys=True,
            )
            if key in selected or not automatic_media_ready(
                media_kind=media_kind,
                asset_kind=snapshot.kind,
                media_path=version.media_path,
                auto_selectable=source.media_semantics.auto_selectable if source.media_semantics else None,
                clip_kind=clip_kind,
                body=version.body_json,
                presentation_approved=source.presentation_approved,
                review_result=snapshot.review_result,
                reference_source=source.model_dump(mode="json"),
                media_content_hash=version.content_hash,
                scene_reference=scene is not None,
                action_clip_kind=source.action.clip_kind if source.action else None,
            ):
                continue
            selected.add(key)
            protected.add(version.id)
    finally:
        await rows.close()
    return protected


async def cleanup_obsolete_assets(db: AsyncSession, user_id: int) -> set[str]:
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
        .values(snapshot_json={}, feedback="", video_feedback="")
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
        return set()
    asset_ids = list(
        await db.scalars(select(VisualVersionAsset.asset_id).where(VisualVersionAsset.version_id.in_(versions)))
    )
    paths = await asset_paths_for_ids(db, user_id, asset_ids)
    await db.execute(delete(VisualVersion).where(VisualVersion.id.in_(versions)))
    return paths
