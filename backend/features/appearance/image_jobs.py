"""头像与全身制作沿公共图片链恢复；采纳事务才完成所属任务。"""

import asyncio
import json
from dataclasses import dataclass

from infrastructure.ai.selection import GenerationOrigin, ProviderSelection
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.references import asset_paths
from infrastructure.database import SESSION_LOCAL
from infrastructure.hashing import sha256_hex
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.jobs.service import bind_job_assets
from features.media.character_images import ImageChainState, generate_reviewed_images
from features.media.image_generation import (
    ImageGenerationError,
    ImageResultDownloadError,
    generate_with_moderation_retry,
)

from .models import AppearanceImageJob


@dataclass(frozen=True)
class AppearanceImageResult:
    path: str
    origin: GenerationOrigin
    job_id: int


async def generate_appearance_image(
    user_id: int,
    prompt: str,
    *,
    request_context: str,
    size: str,
    reference_image: str | None,
    secondary_reference_image: str | None,
    image_edit: bool,
    prefer_transparent_background: bool,
    selection: ProviderSelection | None,
    task: AiTaskKey,
) -> AppearanceImageResult:
    """相同未完成输入复用原结果；调用方持账户外观锁，成功后显式交接到业务资产。"""
    request_hash = sha256_hex(
        json.dumps(
            [
                request_context,
                size,
                reference_image,
                secondary_reference_image,
                image_edit,
                prefer_transparent_background,
                selection.model_dump(mode="json") if selection else None,
                task,
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(AppearanceImageJob)
            .where(
                AppearanceImageJob.user_id == user_id,
                AppearanceImageJob.request_hash == request_hash,
                AppearanceImageJob.state.in_(("queued", "running", "paused", "unknown")),
            )
            .order_by(AppearanceImageJob.id.desc())
            .with_for_update()
        )
        if job is None:
            job = AppearanceImageJob(user_id=user_id, request_hash=request_hash, checkpoint={})
            db.add(job)
        chain = ImageChainState.model_validate(job.checkpoint or {})
        job.state, job.stage, job.error = "running", chain.phase, None
        await db.commit()
        job_id = job.id

    async def save_progress(state: ImageChainState) -> None:
        async with SESSION_LOCAL() as db:
            current = await db.get(AppearanceImageJob, job_id, with_for_update=True)
            if current is None or current.cancel_requested or current.state in {"succeeded", "cancelled"}:
                raise GenerationWorkPaused
            current.checkpoint, current.stage = state.model_dump(mode="json"), state.phase
            await bind_job_assets(db, user_id, job_id, asset_paths(current.checkpoint, user_id), "input")
            await db.commit()

    async def before_submit() -> None:
        require_new_generation_call(user_id)
        async with SESSION_LOCAL() as db:
            if not await user_is_active(db, user_id):
                raise GenerationWorkPaused

    async def generate(text: str) -> list[str]:
        nonlocal chain
        if (
            chain.inputs is not None
            and text != chain.inputs.prompt
            and not (chain.unknown_submission() or chain.pending_urls or chain.candidates)
        ):
            chain = ImageChainState()
        return await generate_reviewed_images(
            text,
            user_id=user_id,
            task=task,
            storage_directory=f"appearance/{job_id}",
            reference_image=reference_image or "",
            identity_reference="",
            size=size,
            secondary_reference_image=secondary_reference_image,
            image_edit=image_edit,
            identity_required=False,
            goal_review="none",
            state=chain,
            save_progress=save_progress,
            before_submit=before_submit,
            prefer_transparent_background=prefer_transparent_background,
            selection=selection,
        )

    try:
        if chain.pending_urls:
            chain.resume_download()
        paths = await generate_with_moderation_retry(
            user_id, chain.inputs.prompt if chain.inputs else prompt, generate, task=task
        )
        origin = chain.selected_origin()
        if origin is None:
            raise ImageGenerationError("图片任务缺少制作来源")
        return AppearanceImageResult(paths[0], origin, job_id)
    except BaseException as exc:
        async with SESSION_LOCAL() as db:
            current = await db.get(AppearanceImageJob, job_id, with_for_update=True)
            if current is not None and current.state not in {"succeeded", "cancelled"}:
                current.state = (
                    "unknown"
                    if chain.unknown_submission() or isinstance(exc, ImageResultDownloadError)
                    else "paused"
                    if chain.pending_urls
                    or chain.pending_path
                    or chain.candidates
                    or isinstance(exc, (asyncio.CancelledError, GenerationWorkPaused))
                    else "failed"
                )
                current.error = "图片制作已保留，重试会继续原任务" if current.state != "failed" else "图片制作失败"
                await db.commit()
        raise


async def complete_appearance_image(db: AsyncSession, user_id: int, job_id: int | None) -> None:
    if job_id is None:
        return
    job = await db.get(AppearanceImageJob, job_id, with_for_update=True)
    if job is None or job.user_id != user_id or job.cancel_requested:
        raise ImageGenerationError("图片制作任务已失效")
    job.state, job.stage, job.error = "succeeded", "complete", None
