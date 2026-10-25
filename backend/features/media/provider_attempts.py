"""图像提交账本与视频队列共用供应商尝试存储。"""

from datetime import datetime
from uuid import uuid4

from infrastructure.text import utc_now
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .chain import FrozenMediaProvider
from .video_queue.models import ProviderAttempt


async def count_scene_submissions(db: AsyncSession, user_id: int, since: datetime) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(ProviderAttempt)
            .where(
                ProviderAttempt.user_id == user_id,
                ProviderAttempt.owner_kind == "scene.autonomous",
                ProviderAttempt.capability == "image",
                ProviderAttempt.enqueued_at >= since,
            )
        )
        or 0
    )


async def record_scene_submission(
    db: AsyncSession,
    user_id: int,
    job_id: int,
    generation_id: str,
    attempt_index: int,
    provider: FrozenMediaProvider,
) -> None:
    await db.execute(
        insert(ProviderAttempt)
        .values(
            token=uuid4().hex,
            job_id=job_id,
            user_id=user_id,
            owner_kind="scene.autonomous",
            capability="image",
            generation_id=generation_id,
            attempt_index=attempt_index,
            frozen_json=provider.model_dump(mode="json"),
            model=provider.model,
            state="submitted",
            revision=0,
            enqueued_at=utc_now(),
            rate_limit_count=0,
            cancel_requested=False,
            pause_requested=False,
        )
        .on_conflict_do_nothing(constraint="uq_video_queue_attempt")
    )
