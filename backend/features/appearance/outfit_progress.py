"""自主造型制作的持久检查点和素材引用。"""

from typing import Any

from infrastructure.database import SESSION_LOCAL
from sqlalchemy import select

from features.jobs.service import bind_job_assets

from .models import OutfitCreationJob


async def read_outfit_progress(user_id: int, job_id: int) -> dict[str, Any]:
    async with SESSION_LOCAL() as db:
        value = await db.scalar(
            select(OutfitCreationJob.checkpoint).where(
                OutfitCreationJob.user_id == user_id, OutfitCreationJob.id == job_id
            )
        )
        return dict(value or {})


async def save_outfit_progress(user_id: int, job_id: int, *, paths: set[str] | None = None, **values: Any) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(OutfitCreationJob)
            .where(OutfitCreationJob.user_id == user_id, OutfitCreationJob.id == job_id)
            .with_for_update()
        )
        if row is None or row.state not in {"running", "queued"}:
            raise ValueError("造型制作任务已失效")
        row.checkpoint = {**(row.checkpoint or {}), **values}
        if paths:
            await bind_job_assets(db, user_id, job_id, paths, "generation")
        await db.commit()
