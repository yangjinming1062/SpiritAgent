"""双图角色分析的持久任务；只有完整发布后才派生初始资产。"""

import asyncio
import base64
import hashlib
import json
from typing import Literal

from components import SESSION_LOCAL, get_logger, parse_llm_json, track_user_task
from modules.companion import AvatarAsset, BodyFeatures, CharacterFeatures, CompanionCharacterCard, PortraitFeatures
from prompts.generation import CHARACTER_CARD_EXTRACTION
from sqlalchemy import select

from services.domains.companion import emit_character_card_updated, get_character_card
from services.infrastructure.llm import vision_chat

from .avatar_service import get_avatar_job_lock, load_avatar_bytes_as_data_uri
from .initial_appearance import start_initial_video
from .room_backdrop_service import schedule_initial_room

logger = get_logger(__name__)
_tasks: dict[int, asyncio.Task[None]] = {}
_reschedule: set[int] = set()
_ANALYSIS_TIMEOUT = 180


def schedule_character_extraction(user_id: int) -> None:
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
    pending = list(_tasks.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def resume_character_extractions() -> None:
    async with SESSION_LOCAL() as db:
        users = (
            await db.scalars(
                select(CompanionCharacterCard.user_id)
                .join(AvatarAsset, AvatarAsset.id == CompanionCharacterCard.avatar_id)
                .where(AvatarAsset.active.is_(True), CompanionCharacterCard.status.in_(("pending", "running"))),
            )
        ).all()
    for user_id in users:
        schedule_character_extraction(user_id)


async def _extract_part(user_id: int, extraction_id: str, part: Literal["portrait", "body"]) -> None:
    async with SESSION_LOCAL() as db:
        card = await get_character_card(db, user_id, lock=True)
        if card is None or card.extraction_id != extraction_id or getattr(card, f"{part}_status") == "ready":
            return
        source_path = getattr(card, f"{part}_source_path")
        setattr(card, f"{part}_status", "running")
        await db.commit()
    result: PortraitFeatures | BodyFeatures | None = None
    source_hash = ""
    error: str | None = None
    try:
        uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, source_path)
        if not uri:
            raise ValueError("source image is unreadable")
        source_hash = hashlib.sha256(base64.b64decode(uri.split(",", 1)[1], validate=True)).hexdigest()
        model = PortraitFeatures if part == "portrait" else BodyFeatures
        async with asyncio.timeout(_ANALYSIS_TIMEOUT):
            raw = await vision_chat(
                user_id,
                CHARACTER_CARD_EXTRACTION,
                json.dumps({"source": part, "schema": model.model_json_schema()}, ensure_ascii=False),
                reference_images=(uri,),
            )
        payload = parse_llm_json(raw)
        # 空字段允许未知特征，缺字段仍属于提取失败。
        if not isinstance(payload, dict) or set(payload) != set(model.model_fields):
            raise ValueError("incomplete character extraction")
        result = model.model_validate(payload)
    except Exception:
        logger.warning("character extraction failed", extra={"user_id": user_id, "part": part}, exc_info=True)
        error = "头像特征分析失败，请重试" if part == "portrait" else "身体特征分析失败，请重试"
    async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
        card = await get_character_card(db, user_id, lock=True)
        if card is None or card.extraction_id != extraction_id:
            return
        avatar = await db.get(AvatarAsset, card.avatar_id)
        if (
            avatar is None
            or avatar.asset_url != card.portrait_source_path
            or avatar.seed_fullbody_url != card.body_source_path
        ):
            error = "参考图片已更新，请重新提取角色资料"
            result = None
        setattr(card, f"{part}_status", "ready" if result is not None else "failed")
        if result is not None:
            setattr(card, f"{part}_result_json", result.model_dump_json())
            setattr(card, f"{part}_pending_hash", source_hash)
        if error:
            card.error = error
        emit_character_card_updated(db, card)
        await db.commit()


async def _extract_character(user_id: int) -> None:
    extraction_id: str | None = None
    try:
        async with SESSION_LOCAL() as db:
            card = await get_character_card(db, user_id, lock=True)
            if card is None or card.status not in ("pending", "running"):
                return
            extraction_id = card.extraction_id
            card.status = "running"
            card.error = None
            emit_character_card_updated(db, card)
            await db.commit()
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
            if card is None or card.extraction_id != extraction_id:
                return
            avatar = await db.get(AvatarAsset, card.avatar_id)
            valid_sources = (
                avatar is not None
                and avatar.asset_url == card.portrait_source_path
                and avatar.seed_fullbody_url == card.body_source_path
            )
            if card.portrait_status == card.body_status == "ready" and valid_sources:
                features = CharacterFeatures(
                    **PortraitFeatures.model_validate_json(card.portrait_result_json).model_dump(),
                    **BodyFeatures.model_validate_json(card.body_result_json).model_dump(),
                )
                card.automatic_json = features.model_dump_json()
                card.portrait_source_hash = card.portrait_pending_hash
                card.body_source_hash = card.body_pending_hash
                card.revision += 1
                card.status = "ready"
                card.error = None
            else:
                card.status = "failed"
                card.error = card.error or "参考图片已更新，请重新提取角色资料"
            ready = card.status == "ready"
            emit_character_card_updated(db, card)
            await db.commit()
        if ready:
            await start_initial_video(user_id)
            await schedule_initial_room(user_id)
    except Exception:
        logger.exception("character card task failed", extra={"user_id": user_id})
        async with SESSION_LOCAL() as db:
            card = await get_character_card(db, user_id, lock=True)
            if card is not None and card.extraction_id == extraction_id and card.status == "running":
                card.status = "failed"
                card.error = "角色资料分析中断，请重试"
                emit_character_card_updated(db, card)
                await db.commit()
