"""共享定义持有冻结评审参考，来源删除后仍保持资产引用。"""

from infrastructure.assets.references import asset_paths
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ActionDefinition


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for source in await db.scalars(
        select(ActionDefinition.review_source_json).where(ActionDefinition.user_id == user_id),
    ):
        referenced |= asset_paths(source, user_id)
    return referenced
