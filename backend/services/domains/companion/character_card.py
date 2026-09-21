"""角色卡资料、并发编辑与生成快照；图像分析任务归生成应用层。"""

import json
from uuid import uuid4

from modules.companion import (
    AvatarAsset,
    CharacterCardResponse,
    CharacterCardSnapshot,
    CharacterCardUpdate,
    CharacterFeatures,
    CharacterOverrides,
    CompanionCharacterCard,
)
from modules.ws import emit_ws_event
from prompts.generation import CHARACTER_IDENTITY_TEMPLATE, CHARACTER_PROFILE_TEMPLATE
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.llm import VisualReasoningError


class CharacterCardConflictError(ValueError):
    pass


class CharacterCardNotReadyError(VisualReasoningError):
    pass


async def get_character_card(db: AsyncSession, user_id: int, *, lock: bool = False) -> CompanionCharacterCard | None:
    query = (
        select(CompanionCharacterCard)
        .join(AvatarAsset, AvatarAsset.id == CompanionCharacterCard.avatar_id)
        .where(CompanionCharacterCard.user_id == user_id, AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True))
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update(of=CompanionCharacterCard)
    return await db.scalar(query)


def character_card_response(card: CompanionCharacterCard) -> CharacterCardResponse:
    automatic = CharacterFeatures.model_validate_json(card.automatic_json)
    overrides = CharacterOverrides.model_validate_json(card.overrides_json)
    return CharacterCardResponse.model_validate(
        {
            "avatar_id": card.avatar_id,
            "revision": card.revision,
            "features": {**automatic.model_dump(), **overrides.model_dump(exclude_none=True)},
            "overrides": overrides,
            "automatic": automatic,
            "status": card.status,
            "portrait_status": card.portrait_status,
            "body_status": card.body_status,
            "error": card.error,
        },
    )


async def load_character_snapshot(
    db: AsyncSession,
    user_id: int,
) -> CharacterCardSnapshot | None:
    card = await get_character_card(db, user_id)
    if card is None or card.revision == 0:
        return None
    response = character_card_response(card)
    return CharacterCardSnapshot(
        avatar_id=response.avatar_id,
        revision=response.revision,
        features=response.features,
        overrides=response.overrides,
    )


async def require_character_snapshot(db: AsyncSession, user_id: int) -> CharacterCardSnapshot:
    snapshot = await load_character_snapshot(db, user_id)
    if snapshot is None:
        raise CharacterCardNotReadyError("角色资料尚未分析完成，请在角色卡中查看进度或重试")
    return snapshot


async def character_snapshot_is_current(db: AsyncSession, user_id: int, snapshot: CharacterCardSnapshot) -> bool:
    card = await get_character_card(db, user_id, lock=True)
    return card is not None and card.avatar_id == snapshot.avatar_id and card.revision == snapshot.revision


def render_character_profile(snapshot: CharacterCardSnapshot | None) -> str:
    if snapshot is None:
        return ""
    return CHARACTER_PROFILE_TEMPLATE.format(features=json.dumps(snapshot.features.model_dump(), ensure_ascii=False))


def render_character_identity(snapshot: CharacterCardSnapshot | None) -> str:
    if snapshot is None:
        return ""
    return CHARACTER_IDENTITY_TEMPLATE.format(
        features=json.dumps(snapshot.features.model_dump(), ensure_ascii=False),
    )


def emit_character_card_updated(db: AsyncSession, card: CompanionCharacterCard) -> None:
    emit_ws_event(
        db,
        user_id=card.user_id,
        event_type="companion.character_card.updated",
        payload={"avatar_id": card.avatar_id, "revision": card.revision, "status": card.status},
    )


def register_character_card(db: AsyncSession, avatar: AvatarAsset) -> CompanionCharacterCard:
    card = CompanionCharacterCard(
        user_id=avatar.user_id,
        avatar_id=avatar.id,
        extraction_id=str(uuid4()),
        portrait_source_path=avatar.asset_url,
        body_source_path=avatar.seed_fullbody_url,
        revision=0,
        status="pending",
    )
    db.add(card)
    emit_character_card_updated(db, card)
    return card


async def update_character_card(db: AsyncSession, user_id: int, request: CharacterCardUpdate) -> CharacterCardResponse:
    card = await get_character_card(db, user_id, lock=True)
    if card is None or card.revision == 0:
        raise CharacterCardNotReadyError("请等待角色资料分析完成后再编辑")
    if card.avatar_id != request.expected_avatar_id or card.revision != request.expected_revision:
        raise CharacterCardConflictError("角色卡已更新，请重新加载后合并修改；当前草稿已保留")
    previous = CharacterOverrides.model_validate_json(card.overrides_json)
    merged = CharacterOverrides.model_validate(
        {**previous.model_dump(), **request.changes.model_dump(exclude_unset=True)},
    )
    if previous != merged:
        card.overrides_json = merged.model_dump_json(exclude_none=True)
        card.revision += 1
        emit_character_card_updated(db, card)
    await db.commit()
    return character_card_response(card)


async def request_character_extraction(
    db: AsyncSession,
    user_id: int,
    *,
    expected_avatar_id: int,
    expected_revision: int,
) -> CharacterCardResponse:
    card = await get_character_card(db, user_id, lock=True)
    if card is None:
        raise CharacterCardNotReadyError("请先确认头像和全身形象")
    if card.avatar_id != expected_avatar_id or card.revision != expected_revision:
        raise CharacterCardConflictError("角色卡已更新，请重新加载")
    if card.status in ("pending", "running"):
        return character_card_response(card)
    avatar = await db.get(AvatarAsset, card.avatar_id)
    if avatar is None or not avatar.is_fullbody_confirmed:
        raise CharacterCardNotReadyError("请先确认全身形象")
    same_sources = card.portrait_source_path == avatar.asset_url and card.body_source_path == avatar.seed_fullbody_url
    keep_parts = card.status == "failed" and same_sources
    if not keep_parts:
        card.portrait_result_json = card.body_result_json = "{}"
        card.portrait_pending_hash = card.body_pending_hash = ""
        card.portrait_status = card.body_status = "pending"
    else:
        if card.portrait_status != "ready":
            card.portrait_status = "pending"
        if card.body_status != "ready":
            card.body_status = "pending"
    card.portrait_source_path = avatar.asset_url
    card.body_source_path = avatar.seed_fullbody_url
    card.extraction_id = str(uuid4())
    card.status = "pending"
    card.error = None
    emit_character_card_updated(db, card)
    await db.commit()
    return character_card_response(card)
