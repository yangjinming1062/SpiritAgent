"""角色卡资料、并发编辑与生成快照；图像分析任务归生成应用层。"""

import json
from uuid import uuid4

from infrastructure.ai.prompt_engineer import VisualReasoningError
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from features.appearance.asset_bindings import commit_appearance, flush_appearance
from features.appearance.character_card_contracts import CharacterCardResponse, CharacterCardUpdate
from features.appearance.models import AvatarAsset, CharacterAnalysisJob
from features.media.identity_contracts import CharacterCardSnapshot, CharacterFeatures, CharacterOverrides
from features.media.prompts import CHARACTER_IDENTITY_TEMPLATE


class CharacterCardConflictError(ValueError):
    pass


class CharacterCardNotReadyError(VisualReasoningError):
    pass


CARD_NOT_ANALYZED = "角色资料尚未分析完成，请在角色卡中查看进度或重试"


async def get_character_card(db: AsyncSession, user_id: int, *, lock: bool = False) -> AvatarAsset | None:
    # 合并后的卡与头像是同一行；刷新锁定视图前必须保存调用方本事务的修改及资产关联。
    if any(
        isinstance(row, (AvatarAsset, CharacterAnalysisJob))
        and row.user_id == user_id
        and (row in db.new or db.is_modified(row, include_collections=False))
        for row in set(db.new) | set(db.dirty)
    ):
        await flush_appearance(db, user_id)
    query = (
        select(AvatarAsset)
        .join(AvatarAsset.analysis_job)
        .options(contains_eager(AvatarAsset.analysis_job))
        .where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True))
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)


def _card_features(card: AvatarAsset) -> tuple[CharacterFeatures, CharacterOverrides, CharacterFeatures]:
    """返回自动特征、用户覆盖与两者合并后的生效特征。"""
    automatic = CharacterFeatures.model_validate(card.automatic_json)
    overrides = CharacterOverrides.model_validate(card.overrides_json)
    features = CharacterFeatures.model_validate({**automatic.model_dump(), **overrides.model_dump(exclude_none=True)})
    return automatic, overrides, features


def extraction_status(state: str) -> str:
    return {"queued": "pending", "running": "running", "succeeded": "ready"}.get(state, "failed")


def character_card_response(card: AvatarAsset) -> CharacterCardResponse:
    automatic, overrides, features = _card_features(card)
    return CharacterCardResponse.model_validate(
        {
            "avatar_id": card.id,
            "revision": card.revision,
            "features": features,
            "overrides": overrides,
            "automatic": automatic,
            "status": extraction_status(card.analysis_job.state),
            "portrait_status": card.analysis_job.portrait_status,
            "body_status": card.analysis_job.body_status,
            "error": card.analysis_job.error,
        },
    )


def card_snapshot(card: AvatarAsset) -> CharacterCardSnapshot:
    """由已加载的角色卡行构造冻结快照；仅处理 revision>0 的卡。"""
    _, overrides, features = _card_features(card)
    return CharacterCardSnapshot(
        avatar_id=card.id,
        revision=card.revision,
        features=features,
        overrides=overrides,
    )


async def load_character_snapshot(
    db: AsyncSession,
    user_id: int,
) -> CharacterCardSnapshot | None:
    card = await get_character_card(db, user_id)
    if card is None or card.revision == 0:
        return None
    return card_snapshot(card)


async def require_character_snapshot(db: AsyncSession, user_id: int) -> CharacterCardSnapshot:
    snapshot = await load_character_snapshot(db, user_id)
    if snapshot is None:
        raise CharacterCardNotReadyError(CARD_NOT_ANALYZED)
    return snapshot


async def require_ready_character_snapshot(db: AsyncSession, user_id: int) -> CharacterCardSnapshot:
    """就绪角色卡快照；未分析、分析失败与分析中分别拒绝。"""
    card = await get_character_card(db, user_id)
    if card is None or card.revision == 0:
        raise CharacterCardNotReadyError(CARD_NOT_ANALYZED)
    if card.analysis_job.state == "failed":
        raise CharacterCardNotReadyError("角色资料分析失败，请先在角色卡中重试分析，再调整全身图")
    if card.analysis_job.state != "succeeded":
        raise CharacterCardNotReadyError("角色资料正在分析，请待分析完成后再调整全身图")
    return card_snapshot(card)


async def character_snapshot_is_current(db: AsyncSession, user_id: int, snapshot: CharacterCardSnapshot) -> bool:
    if snapshot.avatar_id is None:
        return False
    card = await get_character_card(db, user_id, lock=True)
    return card is not None and card.id == snapshot.avatar_id and card.revision == snapshot.revision


def render_character_identity(snapshot: CharacterCardSnapshot | None) -> str:
    if snapshot is None:
        return ""
    features = {key: value for key, value in snapshot.overrides.model_dump(exclude_none=True).items() if value}
    if not features:
        return ""
    return CHARACTER_IDENTITY_TEMPLATE.format(
        features=json.dumps(features, ensure_ascii=False),
    )


def emit_character_card_updated(db: AsyncSession, card: AvatarAsset) -> None:
    emit_ws_event(
        db,
        user_id=card.user_id,
        event_type="companion.character_card.updated",
        payload={"avatar_id": card.id, "revision": card.revision, "status": extraction_status(card.analysis_job.state)},
    )


def register_character_card(db: AsyncSession, avatar: AvatarAsset) -> AvatarAsset:
    avatar.analysis_job = CharacterAnalysisJob(
        user_id=avatar.user_id,
        avatar_id=avatar.id,
        extraction_id=str(uuid4()),
        portrait_source_path=avatar.asset_url,
        body_source_path=avatar.seed_fullbody_url,
        state="queued",
    )
    avatar.revision = 0
    emit_character_card_updated(db, avatar)
    return avatar


async def update_character_card(db: AsyncSession, user_id: int, request: CharacterCardUpdate) -> CharacterCardResponse:
    card = await get_character_card(db, user_id, lock=True)
    if card is None or card.revision == 0:
        raise CharacterCardNotReadyError("请等待角色资料分析完成后再编辑")
    if card.id != request.expected_avatar_id or card.revision != request.expected_revision:
        raise CharacterCardConflictError("角色卡已更新，请重新加载后合并修改；当前草稿已保留")
    previous = CharacterOverrides.model_validate(card.overrides_json)
    merged = CharacterOverrides.model_validate(
        {**previous.model_dump(), **request.changes.model_dump(exclude_unset=True)},
    )
    if previous != merged:
        card.overrides_json = merged.model_dump(mode="json", exclude_none=True)
        card.revision += 1
        emit_character_card_updated(db, card)
    await commit_appearance(db, user_id)
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
    if card.id != expected_avatar_id or card.revision != expected_revision:
        raise CharacterCardConflictError("角色卡已更新，请重新加载")
    if card.analysis_job.state in ("queued", "running"):
        return character_card_response(card)
    avatar = await db.get(AvatarAsset, card.id)
    if avatar is None or not avatar.is_fullbody_confirmed:
        raise CharacterCardNotReadyError("请先确认全身形象")
    same_sources = (
        card.analysis_job.portrait_source_path == avatar.asset_url
        and card.analysis_job.body_source_path == avatar.seed_fullbody_url
    )
    keep_parts = card.analysis_job.state == "failed" and same_sources
    if not keep_parts:
        card.analysis_job.portrait_result_json = card.analysis_job.body_result_json = {}
        card.analysis_job.portrait_status = card.analysis_job.body_status = "pending"
    else:
        if card.analysis_job.portrait_status != "ready":
            card.analysis_job.portrait_status = "pending"
        if card.analysis_job.body_status != "ready":
            card.analysis_job.body_status = "pending"
    card.analysis_job.portrait_source_path = avatar.asset_url
    card.analysis_job.body_source_path = avatar.seed_fullbody_url
    card.analysis_job.extraction_id = str(uuid4())
    card.analysis_job.state = "queued"
    card.analysis_job.error = None
    emit_character_card_updated(db, card)
    await commit_appearance(db, user_id)
    return character_card_response(card)
