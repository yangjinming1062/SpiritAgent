"""动作素材持有冻结评审参考，来源删除后仍保持资产引用。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.references import scanned_asset_paths

from .models import ActionDefinition


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    return await scanned_asset_paths(
        db, user_id, select(ActionDefinition.review_source_json).where(ActionDefinition.user_id == user_id)
    )
