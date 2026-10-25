"""全身透明准备与双图分析的持久任务；完整发布后才派生初始资产。"""

import asyncio
from typing import Literal

from infrastructure.async_io import cancel_and_wait
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.user_maintenance import is_user_in_maintenance, track_user_task
from sqlalchemy import select

from features.appearance.asset_bindings import commit_appearance
from features.appearance.avatar_service import (
    AvatarGenerationError,
    extract_card_features,
    get_avatar_job_lock,
    prepare_initial_fullbody,
)
from features.appearance.character_card import emit_character_card_updated, get_character_card
from features.appearance.completion import notify_character_ready
from features.appearance.models import AvatarAsset, CharacterAnalysisJob
from features.media.identity_contracts import BodyFeatures, CharacterFeatures, PortraitFeatures

logger = get_logger(__name__)
_tasks: dict[int, asyncio.Task[None]] = {}
_reschedule: set[int] = set()


def schedule_character_extraction(user_id: int) -> None:
    if is_user_in_maintenance(user_id):
        return
    task = _tasks.get(user_id)
    if task is not None and not task.done():
        _reschedule.add(user_id)
        return
    _reschedule.discard(user_id)
    task = asyncio.create_task(_extract_character(user_id), name=f"character-card.{user_id}")
    _tasks[user_id] = task
    track_user_task(user_id, task, cancel_on_maintenance=False)

    def completed(done: asyncio.Task[None]) -> None:
        if _tasks.get(user_id) is done:
            _tasks.pop(user_id, None)
        if not done.cancelled() and (error := done.exception()) is not None:
            logger.error("character card task interrupted", extra={"user_id": user_id}, exc_info=error)
        if not done.cancelled() and user_id in _reschedule:
            schedule_character_extraction(user_id)

    task.add_done_callback(completed)


async def drain_character_extractions() -> None:
    _reschedule.clear()
    await cancel_and_wait(list(_tasks.values()))


async def resume_character_extractions() -> None:
    async with SESSION_LOCAL() as db:
        users = (
            await db.scalars(
                select(AvatarAsset.user_id)
                .join(AvatarAsset.analysis_job)
                .where(AvatarAsset.active.is_(True), CharacterAnalysisJob.state.in_(("queued", "running"))),
            )
        ).all()
    for user_id in users:
        schedule_character_extraction(user_id)


async def _extract_part(user_id: int, extraction_id: str, part: Literal["portrait", "body"]) -> None:
    async with SESSION_LOCAL() as db:
        card = await get_character_card(db, user_id, lock=True)
        if (
            card is None
            or card.analysis_job.extraction_id != extraction_id
            or getattr(card.analysis_job, f"{part}_status") == "ready"
        ):
            return
        source_path = getattr(card.analysis_job, f"{part}_source_path")
        setattr(card.analysis_job, f"{part}_status", "running")
        await commit_appearance(db, user_id)
    result: PortraitFeatures | BodyFeatures | None = None
    error: str | None = None

    async def before_submit() -> None:
        require_new_generation_call(user_id)

    try:
        result = await extract_card_features(
            user_id,
            source_path,
            PortraitFeatures if part == "portrait" else BodyFeatures,
            before_submit=before_submit,
        )
    except GenerationWorkPaused:
        raise
    except Exception:
        logger.warning("character extraction failed", extra={"user_id": user_id, "part": part}, exc_info=True)
        error = "头像特征分析失败，请重试" if part == "portrait" else "身体特征分析失败，请重试"
    async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
        card = await get_character_card(db, user_id, lock=True)
        if card is None or card.analysis_job.extraction_id != extraction_id:
            return
        avatar = await db.get(AvatarAsset, card.id)
        if (
            avatar is None
            or avatar.asset_url != card.analysis_job.portrait_source_path
            or avatar.seed_fullbody_url != card.analysis_job.body_source_path
        ):
            error = "参考图片已更新，请重新提取角色资料"
            result = None
        setattr(card.analysis_job, f"{part}_status", "ready" if result is not None else "failed")
        if result is not None:
            setattr(card.analysis_job, f"{part}_result_json", result.model_dump(mode="json"))
        if error:
            card.analysis_job.error = error
        emit_character_card_updated(db, card)
        await commit_appearance(db, user_id)


async def _extract_character(user_id: int) -> None:
    extraction_id: str | None = None
    try:
        async with SESSION_LOCAL() as db:
            card = await get_character_card(db, user_id, lock=True)
            if card is None or card.analysis_job.state not in ("queued", "running"):
                return
            extraction_id = card.analysis_job.extraction_id
            card.analysis_job.state = "running"
            card.analysis_job.error = None
            emit_character_card_updated(db, card)
            await commit_appearance(db, user_id)
        if not await prepare_initial_fullbody(user_id, extraction_id=extraction_id):
            return
        results = await asyncio.gather(
            _extract_part(user_id, extraction_id, "portrait"),
            _extract_part(user_id, extraction_id, "body"),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
            card = await get_character_card(db, user_id, lock=True)
            if card is None or card.analysis_job.extraction_id != extraction_id:
                return
            avatar = await db.get(AvatarAsset, card.id)
            valid_sources = (
                avatar is not None
                and avatar.asset_url == card.analysis_job.portrait_source_path
                and avatar.seed_fullbody_url == card.analysis_job.body_source_path
            )
            if card.analysis_job.portrait_status == card.analysis_job.body_status == "ready" and valid_sources:
                features = CharacterFeatures(
                    **PortraitFeatures.model_validate(card.analysis_job.portrait_result_json).model_dump(),
                    **BodyFeatures.model_validate(card.analysis_job.body_result_json).model_dump(),
                )
                card.automatic_json = features.model_dump(mode="json")
                card.revision += 1
                card.analysis_job.state = "succeeded"
                card.analysis_job.error = None
            else:
                card.analysis_job.state = "failed"
                card.analysis_job.error = card.analysis_job.error or "参考图片已更新，请重新提取角色资料"
            ready = card.analysis_job.state == "succeeded"
            emit_character_card_updated(db, card)
            await commit_appearance(db, user_id)
        if ready:
            await notify_character_ready(user_id)
    except GenerationWorkPaused:
        return
    except Exception as exc:
        logger.exception("character card task failed", extra={"user_id": user_id})
        async with SESSION_LOCAL() as db:
            card = await get_character_card(db, user_id, lock=True)
            if (
                card is not None
                and card.analysis_job.extraction_id == extraction_id
                and card.analysis_job.state == "running"
            ):
                card.analysis_job.state = "failed"
                if isinstance(exc, AvatarGenerationError):
                    card.analysis_job.body_status = "failed"
                    card.analysis_job.error = str(exc)
                else:
                    card.analysis_job.error = "角色资料分析中断，请重试"
                emit_character_card_updated(db, card)
                await commit_appearance(db, user_id)


async def resume_user_character_extraction(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        card = await get_character_card(db, user_id)
        pending = card is not None and card.analysis_job.state in ("queued", "running")
    if pending:
        schedule_character_extraction(user_id)
