from infrastructure.assets.references import asset_paths
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DesktopVideoAction, DesktopVideoProposal, DesktopVideoSet


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for model, fields in (
        (DesktopVideoSet, ("context_json", "base_image_path")),
        (DesktopVideoAction, ("generation_state_json", "accepted_asset_json")),
        (DesktopVideoProposal, ("design_json",)),
    ):
        for row in await db.scalars(select(model).where(model.user_id == user_id)):
            referenced |= asset_paths([getattr(row, field, None) for field in fields], user_id)
    return referenced
