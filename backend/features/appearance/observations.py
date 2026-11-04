"""可选的视觉观察，只供无图的对话与规划使用。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.appearance.models import AvatarAsset, VisualObservationJob
from features.media.identity_contracts import VisualObservation


async def load_visual_observation(db: AsyncSession, user_id: int) -> VisualObservation | None:
    result = await db.scalar(
        select(VisualObservationJob.result_json)
        .join(AvatarAsset, AvatarAsset.id == VisualObservationJob.avatar_id)
        .where(
            AvatarAsset.user_id == user_id,
            AvatarAsset.active.is_(True),
            AvatarAsset.identity_revision >= 1,
            AvatarAsset.identity_revision == VisualObservationJob.identity_revision,
            VisualObservationJob.state == "succeeded",
        )
    )
    return VisualObservation.model_validate(result) if result else None
