"""透明素材准备与非阻塞视觉观察各自持久化、独立恢复。"""

import asyncio

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.assets.asset_store import read_asset_data_uri
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.background import TaskScope
from runtime.events.emit import emit_ws_event
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import select

from features.appearance.asset_bindings import commit_appearance
from features.appearance.avatar_service import AvatarGenerationError, prepare_initial_fullbody
from features.appearance.identity import emit_preparation_updated, get_visual_identity_asset
from features.appearance.models import (
    PREPARATION_RESUMABLE_STATES,
    AppearancePreparationJob,
    AvatarAsset,
    VisualObservationJob,
)
from features.appearance.prompts import VISUAL_OBSERVATION_PROMPT, VISUAL_OBSERVATION_REPAIR
from features.media.identity_contracts import VisualObservation

logger = get_logger(__name__)
_tasks = TaskScope("appearance.preparation")


def schedule_appearance_tasks(user_id: int) -> None:
    if is_user_in_maintenance(user_id):
        return
    _tasks.schedule(
        user_id,
        lambda: _run_user(user_id),
        name=f"appearance.{user_id}",
        user_id=user_id,
        cancel_on_maintenance=False,
    )


async def _run_user(user_id: int) -> None:
    if is_user_in_maintenance(user_id):
        return
    results = await asyncio.gather(_prepare(user_id), _observe(user_id), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


async def _prepare(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        avatar = await get_visual_identity_asset(db, user_id, lock=True)
        if (
            avatar is None
            or avatar.preparation_job is None
            or avatar.preparation_job.state not in PREPARATION_RESUMABLE_STATES
        ):
            return
        job = avatar.preparation_job
        job_id, preparation_id, avatar_id = job.id, job.preparation_id, avatar.id
        job.state, job.error = "running", None
        emit_preparation_updated(db, user_id, avatar_id)
        await commit_appearance(db, user_id)
    error: str | None = None
    try:
        ready = await prepare_initial_fullbody(user_id, preparation_id=preparation_id)
        if not ready:
            error = "确认形象已更新，原准备任务已结束"
    except Exception as exc:
        logger.exception("initial appearance preparation failed", extra={"user_id": user_id})
        error = str(exc) if isinstance(exc, AvatarGenerationError) else "形象素材准备暂未完成，请重试"
    async with SESSION_LOCAL() as db:
        avatar = await get_visual_identity_asset(db, user_id, lock=True)
        job = await db.get(AppearancePreparationJob, job_id, with_for_update=True)
        if job is None or job.state != "running":
            return
        if avatar is None or avatar.preparation_job_id != job_id or avatar.identity_revision != job.identity_revision:
            job.state, job.error = "cancelled", "确认形象已更新"
        else:
            job.state, job.error = ("failed", error) if error else ("succeeded", None)
            emit_preparation_updated(db, user_id, avatar_id)
            if error is None:
                emit_ws_event(db, user_id=user_id, event_type="companion.visual.initial_requested", payload={})
        await commit_appearance(db, user_id)


async def _observe(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        rows = list(
            await db.scalars(
                select(VisualObservationJob.id).where(
                    VisualObservationJob.user_id == user_id, VisualObservationJob.state.in_(("queued", "running"))
                )
            )
        )
    for job_id in rows:
        while True:
            async with SESSION_LOCAL() as db:
                job = await db.get(VisualObservationJob, job_id, with_for_update=True)
                if job is None or job.state not in ("queued", "running"):
                    break
                avatar = await db.get(AvatarAsset, job.avatar_id)
                if avatar is None or not avatar.active or avatar.identity_revision != job.identity_revision:
                    job.state, job.error = "cancelled", "确认形象已更新"
                    await db.commit()
                    break
                if job.attempts >= 3:
                    job.state, job.error = "failed", job.error or "视觉观察已达到重试上限"
                    await db.commit()
                    break
                paths = (job.portrait_source_path, job.body_source_path)
                revision = job.identity_revision
                try:
                    require_new_generation_call(user_id)
                except GenerationWorkPaused:
                    return
                job.attempts += 1
                attempt = job.attempts
                job.state = "running"
                await db.commit()
            result: VisualObservation | None = None
            error: str | None = None
            try:
                references = tuple(
                    await asyncio.gather(*(asyncio.to_thread(read_asset_data_uri, path) for path in paths))
                )
                if not all(references):
                    raise ValueError("确认参考图无法读取")
                config = await load_user_llm_config(
                    user_id,
                    AiTaskKey.appearance,
                    required_modalities=frozenset({ModelModality.text, ModelModality.image}),
                )

                async def before_call() -> None:
                    require_new_generation_call(user_id)
                    async with SESSION_LOCAL() as db:
                        current_avatar = await db.get(AvatarAsset, job.avatar_id)
                        if (
                            current_avatar is None
                            or not current_avatar.active
                            or current_avatar.identity_revision != revision
                        ):
                            raise LlmCallBlockedError("确认形象已更新")

                result = await call_llm_json(
                    config,
                    VISUAL_OBSERVATION_PROMPT,
                    {"reference_order": ["portrait", "fullbody"]},
                    schema=VisualObservation.model_json_schema(),
                    schema_name="visual_observation",
                    parse_output=VisualObservation.model_validate,
                    reference_images=references,
                    repair_prompt=VISUAL_OBSERVATION_REPAIR,
                    before_call=before_call,
                )
            except GenerationWorkPaused:
                async with SESSION_LOCAL() as db:
                    job = await db.get(VisualObservationJob, job_id)
                    if job is not None:
                        job.state = "queued"
                        job.attempts -= 1
                        await db.commit()
                return
            except Exception:
                logger.warning(
                    "visual observation failed", extra={"user_id": user_id, "attempt": attempt}, exc_info=True
                )
                error = "视觉观察暂不可用"
            async with SESSION_LOCAL() as db:
                job = await db.get(VisualObservationJob, job_id, with_for_update=True)
                if job is None:
                    break
                avatar = await db.get(AvatarAsset, job.avatar_id)
                current = (
                    avatar is not None
                    and avatar.active
                    and avatar.identity_revision == revision
                    and (avatar.asset_url, avatar.seed_fullbody_url) == paths
                )
                if not current:
                    job.state, job.error = "cancelled", "确认形象已更新"
                elif result is not None:
                    job.result_json = result.model_dump(mode="json")
                    job.state, job.error = "succeeded", None
                else:
                    job.state, job.error = ("failed" if attempt >= 3 else "queued"), error
                again = job.state == "queued"
                await db.commit()
            if not again:
                break
            await asyncio.sleep(min(attempt * 2, 5))


async def resume_appearance_tasks() -> None:
    async with SESSION_LOCAL() as db:
        users = set(
            await db.scalars(
                select(AppearancePreparationJob.user_id).where(
                    AppearancePreparationJob.state.in_(PREPARATION_RESUMABLE_STATES)
                )
            )
        )
        users.update(
            await db.scalars(
                select(VisualObservationJob.user_id).where(VisualObservationJob.state.in_(("queued", "running")))
            )
        )
    for user_id in users:
        schedule_appearance_tasks(user_id)


async def resume_user_appearance_tasks(user_id: int) -> None:
    schedule_appearance_tasks(user_id)


async def drain_appearance_tasks() -> None:
    await _tasks.drain()
