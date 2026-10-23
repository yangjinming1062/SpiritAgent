from infrastructure.assets.references import asset_paths
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import VisualItem, VisualJob, VisualPlayback, VisualPresentation


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for model, fields in (
        (VisualItem, ("media_path", "poster_path", "input_image_path", "snapshot_json")),
        (VisualJob, ("snapshot_json", "progress_json", "candidates_json")),
        (VisualPresentation, ("current_snapshot_json", "loop_snapshot_json", "pending_command_json")),
    ):
        for row in await db.scalars(select(model).where(model.user_id == user_id)):
            referenced |= asset_paths([getattr(row, field) for field in fields], user_id)
    for play in await db.scalars(
        select(VisualPlayback).where(
            VisualPlayback.user_id == user_id,
            VisualPlayback.status.in_(("queued", "claimed", "started")),
        ),
    ):
        referenced |= asset_paths(play.snapshot_json, user_id)
    return referenced
