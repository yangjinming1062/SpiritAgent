"""环境采纳资源与制作依赖显式关联到资产登记。"""

from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.assets.references import asset_paths
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.service import asset_paths_in_session, ensure_assets_in_session
from features.jobs.service import bind_job_asset_ids, job_asset_paths

from .models import CompanionScene


async def sync_scene_assets(db: AsyncSession, row: CompanionScene) -> None:
    initial = asset_paths([row.reference_image, row.initial_job.checkpoint if row.initial_job else None], row.user_id)
    replacement = asset_paths(row.replacement_job.checkpoint if row.replacement_job else None, row.user_id)
    paths = initial | replacement | {path for path in (row.media_path, row.upload_source_path) if path}
    existing = {
        path for path in paths if (resolved := resolve_asset_reference(path)) is not None and resolved[0].is_file()
    }
    if not existing:
        return
    assets = await ensure_assets_in_session(db, row.user_id, existing)
    row.media_asset_id = assets.get(row.media_path)
    row.upload_asset_id = assets.get(row.upload_source_path)
    await db.flush()
    if row.initial_job is not None:
        await bind_job_asset_ids(
            db, row.user_id, row.initial_job.id, [assets[path] for path in initial if path in assets], "dependency"
        )
    if row.replacement_job is not None:
        await bind_job_asset_ids(
            db,
            row.user_id,
            row.replacement_job.id,
            [assets[path] for path in replacement if path in assets],
            "dependency",
        )


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    rows = await db.execute(
        select(CompanionScene.media_asset_id, CompanionScene.upload_asset_id).where(CompanionScene.user_id == user_id)
    )
    asset_ids = {asset_id for row in rows for asset_id in row if asset_id is not None}
    return set((await asset_paths_in_session(db, user_id, asset_ids)).values()) | await job_asset_paths(db, user_id)
