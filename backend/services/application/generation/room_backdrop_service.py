"""伙伴房间图生命周期。

触发点：onboarding 形象确认 / 用户 HTTP 换房 / 在线 LLM 工具换房 / 夜间自主规划与着装对齐。
同 persona 同时只允许一个 pending（CAS），新请求把旧 pending 标 superseded。
ready 行同步设 active（除非政策在自主生成期间被锁定）；保留最近 N 张 ready 供回滚，用户可手动删除非当前历史行。
故障人格化：失败后写 error_utterance 给 Client 朗读；最多 3 次尝试。
"""

import asyncio
import base64
import io
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    REMOTE_ASSET_DOWNLOAD_MAX_BYTES,
    ROOM_BACKDROP_DOWNLOAD_MAX_BYTES,
    ROOM_BACKDROP_FAILURES_TOTAL,
    ROOM_BACKDROP_IMAGES_TOTAL,
    SESSION_LOCAL,
    SETTINGS,
    download_capped,
    get_file_path,
    get_logger,
    log_paid_call,
    parse_llm_json,
    track_user_task,
    utc_now,
)
from modules.companion import (
    AvatarAsset,
    BackdropIntent,
    BackdropOrigin,
    BackdropPolicy,
    BackdropSource,
    BackdropStatus,
    CharacterCardSnapshot,
    CompanionOutfit,
    CompanionRoomBackdrop,
    MomentKind,
    Persona,
)
from modules.ws import emit_ws_event
from PIL import Image
from prompts.generation import ROOM_BRIEF_SYSTEM
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import (
    CharacterCardNotReadyError,
    character_snapshot_is_current,
    load_character_snapshot,
    load_persona_definition,
    require_character_snapshot,
)
from services.domains.journal import create_user_moment
from services.infrastructure.assets import asset_store
from services.infrastructure.llm import call_llm_once, resolve_reference_bytes, resolve_user_llm_config

from .avatar_service import load_character_reference_data_uri
from .image_generation import ImageGenerationError, generate_images
from .room_prompt import RoomPromptContext, build_room_prompt

logger = get_logger(__name__)

# 进程内每用户锁：生图与 activate 走同一锁防并发把同一 persona 的 active 状态撞出多行。
_BACKDROP_LOCKS: dict[int, asyncio.Lock] = {}
# 进程内任务表；规模小、本期不接入启动恢复。
_INFLIGHT_TASKS: dict[int, asyncio.Task[None]] = {}

_DEFAULT_FAILURE_UTTERANCE = "房间还没收拾完，你先坐一会儿。"
_ONE_DAY = timedelta(days=1)
_AUTONOMOUS_ORIGINS = frozenset(
    (BackdropOrigin.LLM.value, BackdropOrigin.NIGHTLY.value),
)
_MIN_IMAGE_BYTES: int = 4 * 1024
_VALID_IMAGE_MAGIC: tuple[bytes, ...] = (
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"RIFF",
    b"GIF8",
    b"BM",
)


class RoomBackdropError(RuntimeError):
    """房间图流程错误；str(exc) 恒为可展示的公开文案。"""


class RoomBackdropNotFoundError(RoomBackdropError):
    pass


class RoomBackdropStateError(RoomBackdropError):
    pass


class RoomBackdropLockedError(RoomBackdropError):
    """政策锁住导致 LLM 主动换房被拒。"""


class RoomBackdropQuotaExceededError(RoomBackdropError):
    """24h 主动配额用尽。"""


def _backdrop_lock(user_id: int) -> asyncio.Lock:
    return _BACKDROP_LOCKS.setdefault(user_id, asyncio.Lock())


async def _emit_backdrop_event(
    user_id: int,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """统一事件写入口：失败只记日志，不冒泡影响主流程。"""
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type=event_type, payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit %s", event_type, exc_info=True)


async def get_active_backdrop(
    db: AsyncSession,
    user_id: int,
    *,
    persona: Persona | None = None,
) -> CompanionRoomBackdrop | None:
    if persona is None:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    if persona is None or persona.active_backdrop_id is None:
        return None
    row = (
        await db.execute(
            select(CompanionRoomBackdrop).where(
                CompanionRoomBackdrop.id == persona.active_backdrop_id,
                CompanionRoomBackdrop.user_id == user_id,
                CompanionRoomBackdrop.status == BackdropStatus.READY.value,
            ),
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    return (
        await db.execute(
            select(CompanionRoomBackdrop)
            .where(
                CompanionRoomBackdrop.user_id == user_id,
                CompanionRoomBackdrop.status == BackdropStatus.READY.value,
            )
            .order_by(
                CompanionRoomBackdrop.ready_at.desc().nullslast(),
                CompanionRoomBackdrop.id.desc(),
            )
            .limit(1),
        )
    ).scalar_one_or_none()


async def get_pending_backdrop(
    db: AsyncSession,
    user_id: int,
) -> CompanionRoomBackdrop | None:
    return (
        await db.execute(
            select(CompanionRoomBackdrop)
            .where(
                CompanionRoomBackdrop.user_id == user_id,
                CompanionRoomBackdrop.status == BackdropStatus.PENDING.value,
            )
            .order_by(CompanionRoomBackdrop.id.desc())
            .limit(1),
        )
    ).scalar_one_or_none()


async def get_backdrop(
    db: AsyncSession,
    user_id: int,
    backdrop_id: int,
) -> CompanionRoomBackdrop | None:
    return (
        await db.execute(
            select(CompanionRoomBackdrop).where(
                CompanionRoomBackdrop.id == backdrop_id,
                CompanionRoomBackdrop.user_id == user_id,
            ),
        )
    ).scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class RoomState:
    active: CompanionRoomBackdrop | None
    history: list[CompanionRoomBackdrop]
    policy: str
    pending: CompanionRoomBackdrop | None


async def get_room_state(db: AsyncSession, user_id: int) -> RoomState:
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    active = await get_active_backdrop(db, user_id, persona=persona)
    pending = await get_pending_backdrop(db, user_id)
    history_rows = (
        (
            await db.execute(
                select(CompanionRoomBackdrop)
                .where(
                    CompanionRoomBackdrop.user_id == user_id,
                    CompanionRoomBackdrop.status == BackdropStatus.READY.value,
                )
                .order_by(
                    CompanionRoomBackdrop.ready_at.desc().nullslast(),
                    CompanionRoomBackdrop.id.desc(),
                ),
            )
        )
        .scalars()
        .all()
    )
    return RoomState(
        active=active,
        history=list(history_rows),
        policy=(persona.backdrop_policy if persona is not None else BackdropPolicy.LLM_MAY_REPLACE.value),
        pending=pending,
    )


async def set_backdrop_policy(db: AsyncSession, user_id: int, policy: str) -> str:
    if policy not in (
        BackdropPolicy.LOCKED.value,
        BackdropPolicy.LLM_MAY_REPLACE.value,
    ):
        raise RoomBackdropStateError(f"unknown backdrop policy: {policy}")
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    if persona is None:
        persona = Persona(user_id=user_id, definition_json="{}")
        db.add(persona)
        await db.flush()
    persona.backdrop_policy = policy
    await db.commit()
    return policy


async def activate_backdrop(
    db: AsyncSession,
    user_id: int,
    backdrop_id: int,
) -> CompanionRoomBackdrop:
    """把指定 ready 行切为 active；outfit_fingerprint 与当前穿着不一致时回 409 风格的错误。"""
    async with _backdrop_lock(user_id):
        target = await get_backdrop(db, user_id, backdrop_id)
        if target is None:
            raise RoomBackdropNotFoundError(f"backdrop {backdrop_id} not found")
        if target.status != BackdropStatus.READY.value:
            raise RoomBackdropStateError("backdrop is not ready")
        current_fingerprint = await _current_outfit_fingerprint(db, user_id)
        if (current_fingerprint or target.outfit_fingerprint) and target.outfit_fingerprint != current_fingerprint:
            raise RoomBackdropStateError(
                "backdrop is from a previous outfit; rebuild before activating",
            )
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None:
            persona = Persona(user_id=user_id, definition_json="{}")
            db.add(persona)
            await db.flush()
        persona.active_backdrop_id = target.id
        target.origin = BackdropOrigin.ROLLBACK.value
        await db.commit()
        await db.refresh(target)
        await _emit_backdrop_event(
            user_id,
            "companion.room.ready",
            _event_payload(target),
        )
        return target


@dataclass(frozen=True, slots=True)
class RoomOutfitReconcileResult:
    """夜间房间—着装对齐结果；outcome 见 reconcile_room_outfit。"""

    outcome: str
    backdrop_id: int | None = None
    reason: str = ""


async def reconcile_room_outfit(user_id: int) -> RoomOutfitReconcileResult:
    """夜间流水线：房间图着装指纹与当前穿着对齐。

    换装成功不即时重建房间（避免频繁换装触发大量生图）。本函数由夜间调用：
    - 指纹一致 → no-op；
    - 历史中存在指纹匹配的 ready 图 → 只切 active 指针复用，不生图；
    - 否则 schedule origin=nightly 重建（受房间自主政策锁定约束）。
    """
    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete:
            return RoomOutfitReconcileResult("skipped", reason="persona not ready")
        if persona.backdrop_policy == BackdropPolicy.LOCKED.value:
            return RoomOutfitReconcileResult("skipped", reason="room locked")

        current_fingerprint = await _current_outfit_fingerprint(db, user_id)
        active = await get_active_backdrop(db, user_id, persona=persona)
        if active is not None:
            active_fingerprint = active.outfit_fingerprint or ""
            if not (current_fingerprint or active_fingerprint) or active_fingerprint == current_fingerprint:
                return RoomOutfitReconcileResult("consistent", backdrop_id=active.id)
        elif not current_fingerprint:
            return RoomOutfitReconcileResult("consistent", reason="no active backdrop and no ready outfit")
        else:
            has_backdrop = (
                await db.execute(
                    select(CompanionRoomBackdrop.id).where(CompanionRoomBackdrop.user_id == user_id).limit(1),
                )
            ).scalar_one_or_none()
            if has_backdrop is None:
                return RoomOutfitReconcileResult("skipped", reason="no existing room backdrop to reconcile")

        pending = await get_pending_backdrop(db, user_id)
        if pending is not None:
            if pending.source == BackdropSource.USER_UPLOAD.value:
                return RoomOutfitReconcileResult(
                    "skipped",
                    backdrop_id=pending.id,
                    reason="pending user upload in progress",
                )
            if pending.outfit_fingerprint == current_fingerprint and await character_snapshot_is_current(
                db,
                user_id,
                CharacterCardSnapshot.model_validate_json(pending.character_card_json),
            ):
                return RoomOutfitReconcileResult(
                    "rebuild_scheduled",
                    backdrop_id=pending.id,
                    reason="already pending for current outfit",
                )

        candidate = (
            await db.execute(
                select(CompanionRoomBackdrop)
                .where(
                    CompanionRoomBackdrop.user_id == user_id,
                    CompanionRoomBackdrop.status == BackdropStatus.READY.value,
                    CompanionRoomBackdrop.outfit_fingerprint == current_fingerprint,
                )
                .order_by(
                    CompanionRoomBackdrop.ready_at.desc().nullslast(),
                    CompanionRoomBackdrop.id.desc(),
                )
                .limit(1),
            )
        ).scalar_one_or_none()
        if candidate is not None and await character_snapshot_is_current(
            db,
            user_id,
            CharacterCardSnapshot.model_validate_json(candidate.character_card_json),
        ):
            if pending is not None:
                await _supersede_pending(db, user_id)
                _cancel_inflight_task(user_id)
            persona.active_backdrop_id = candidate.id
            await db.commit()
            await db.refresh(candidate)
            await _emit_backdrop_event(
                user_id,
                "companion.room.ready",
                _event_payload(candidate),
            )
            return RoomOutfitReconcileResult("reused", backdrop_id=candidate.id)

    try:
        row = await schedule_room_generation(
            user_id,
            origin=BackdropOrigin.NIGHTLY.value,
            intent=BackdropIntent.REBUILD.value,
            notes=None,
        )
    except RoomBackdropError as exc:
        return RoomOutfitReconcileResult("skipped", reason=str(exc))
    return RoomOutfitReconcileResult("rebuild_scheduled", backdrop_id=row.id)


async def _room_identity(db: AsyncSession, user_id: int) -> CharacterCardSnapshot:
    try:
        return await require_character_snapshot(db, user_id)
    except CharacterCardNotReadyError as exc:
        raise RoomBackdropStateError(str(exc)) from exc


async def _current_outfit_fingerprint(db: AsyncSession, user_id: int) -> str:
    outfit = (
        await db.execute(
            select(CompanionOutfit)
            .where(
                CompanionOutfit.user_id == user_id,
                CompanionOutfit.active.is_(True),
                CompanionOutfit.status == "ready",
            )
            .order_by(CompanionOutfit.id.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    if outfit is None:
        return ""
    return str(outfit.id)


async def _consume_llm_quota(db: AsyncSession, user_id: int) -> None:
    """每用户每 24h origin=llm 成功数 ≤ 1；用户回合与夜间任务不占此配额。"""
    limit = int(SETTINGS.room_llm_replace_per_24h)
    if limit <= 0:
        return
    since = utc_now() - _ONE_DAY
    count = (
        await db.execute(
            select(func.count(CompanionRoomBackdrop.id)).where(
                CompanionRoomBackdrop.user_id == user_id,
                CompanionRoomBackdrop.origin == BackdropOrigin.LLM.value,
                CompanionRoomBackdrop.status == BackdropStatus.READY.value,
                CompanionRoomBackdrop.ready_at >= since,
            ),
        )
    ).scalar_one()
    if count >= limit:
        raise RoomBackdropQuotaExceededError("今天已经换过房了，明天再来吧。")


async def _supersede_pending(db: AsyncSession, user_id: int) -> None:
    """同一 persona 只允许一个 pending；新请求把旧 pending 标 superseded。"""
    await db.execute(
        update(CompanionRoomBackdrop)
        .where(
            CompanionRoomBackdrop.user_id == user_id,
            CompanionRoomBackdrop.status == BackdropStatus.PENDING.value,
        )
        .values(status=BackdropStatus.SUPERSEDED.value),
    )


async def schedule_room_generation(
    user_id: int,
    *,
    origin: str,
    intent: str = "rebuild",
    notes: str | None = None,
    reference_image: str | None = None,
) -> CompanionRoomBackdrop:
    """创建 pending 行；返回行对象供 HTTP 端点返回 202。后续生图走 fire-and-forget。"""
    # 先读取并校验用户图，避免坏图取代当前 pending；快照贯穿本次任务的全部重试。
    if reference_image is not None:
        reference_image = await _prepare_reference_image(reference_image)
    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        if origin == BackdropOrigin.ONBOARDING.value:
            existing = await db.scalar(
                select(CompanionRoomBackdrop.id).where(CompanionRoomBackdrop.user_id == user_id).limit(1),
            )
            if existing is not None:
                raise RoomBackdropStateError("初始房间已准备，不重复创建")
        if origin == BackdropOrigin.LLM.value:
            await _consume_llm_quota(db, user_id)
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete:
            raise RoomBackdropStateError("persona not ready; complete onboarding first")
        if origin in _AUTONOMOUS_ORIGINS and persona.backdrop_policy == BackdropPolicy.LOCKED.value:
            raise RoomBackdropLockedError("房间已被你锁住，想换就解开再说。")
        identity = await _room_identity(db, user_id)
        await _supersede_pending(db, user_id)
        outfit_fingerprint = await _current_outfit_fingerprint(db, user_id)
        row = CompanionRoomBackdrop(
            user_id=user_id,
            status=BackdropStatus.PENDING.value,
            origin=origin,
            intent=intent,
            outfit_fingerprint=outfit_fingerprint,
            character_card_json=identity.model_dump_json(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    _launch_generation_task(
        row.id,
        user_id,
        origin=origin,
        intent=intent,
        notes=notes,
        reference_image=reference_image,
    )
    return row


async def _current_outfit_description(user_id: int) -> str:
    """当前穿着外观的描述原文；无就绪外观返回空串（提示词不写穿着块）。"""
    async with SESSION_LOCAL() as db:
        outfit = (
            await db.execute(
                select(CompanionOutfit)
                .where(
                    CompanionOutfit.user_id == user_id,
                    CompanionOutfit.active.is_(True),
                    CompanionOutfit.status == "ready",
                )
                .order_by(CompanionOutfit.id.desc())
                .limit(1),
            )
        ).scalar_one_or_none()
    return (outfit.description or "").strip() if outfit is not None else ""


async def schedule_room_prompt(
    user_id: int,
    *,
    intent: str = "rebuild",
    notes: str | None = None,
) -> CompanionRoomBackdrop:
    """准备自备图记录，保存并返回外部制作提示词。"""
    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete:
            raise RoomBackdropStateError("persona not ready; complete onboarding first")
        identity = await _room_identity(db, user_id)
        avatar = (
            await db.execute(
                select(AvatarAsset).where(
                    AvatarAsset.user_id == user_id,
                    AvatarAsset.active.is_(True),
                ),
            )
        ).scalar_one_or_none()
        if (
            avatar is None
            or not avatar.seed_fullbody_url
            or await asyncio.to_thread(
                load_character_reference_data_uri,
                avatar,
            )
            is None
        ):
            raise RoomBackdropStateError("全身形象缺失或无法读取，请在设置的“角色与记忆”中重新生成")
        await _supersede_pending(db, user_id)
        row = CompanionRoomBackdrop(
            user_id=user_id,
            status=BackdropStatus.PENDING.value,
            origin=BackdropOrigin.USER_REQUEST.value,
            intent=intent,
            outfit_fingerprint=await _current_outfit_fingerprint(db, user_id),
            source=BackdropSource.USER_UPLOAD.value,
            character_card_json=identity.model_dump_json(),
        )
        db.add(row)
        await db.commit()
        row_id = row.id
    # 被取代的在飞 AI 生成任务不再有人消费其结果，立即取消省一次完整生图往返
    _cancel_inflight_task(user_id)

    brief = await _compose_brief(user_id, intent=intent, notes=notes)
    prompt = build_room_prompt(
        RoomPromptContext(
            intent=intent,
            outfit_description=await _current_outfit_description(user_id),
            brief=brief,
            notes=notes or "",
        ),
    )
    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        fresh = (
            await db.execute(
                select(CompanionRoomBackdrop).where(
                    CompanionRoomBackdrop.id == row_id,
                    CompanionRoomBackdrop.user_id == user_id,
                    CompanionRoomBackdrop.status == BackdropStatus.PENDING.value,
                ),
            )
        ).scalar_one_or_none()
        if fresh is None:
            # 已被新请求取代时只返回制作资料，不再更新数据库。
            row.brief = brief
            row.prompt = prompt
            return row
        fresh.brief = brief
        fresh.prompt = prompt
        await db.commit()
        await db.refresh(fresh)
    return fresh


async def adopt_room_backdrop(
    user_id: int,
    backdrop_id: int | None,
    *,
    data: bytes,
) -> CompanionRoomBackdrop:
    """自备图采纳：校验用户上传的房间图并落库，行按生成链同一激活/事件语义转 ready。
    有目标行时沿用其提示词与着装快照；直接上传先校验图片，再创建本次采纳的待提交行。"""
    try:
        data, mime = await asyncio.to_thread(_decode_reference_image, data)
    except Exception as exc:
        raise RoomBackdropError("图片无法读取，请换一张有效的 PNG / JPEG / WebP / GIF 图片") from exc

    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        identity = await _room_identity(db, user_id)
        if backdrop_id is None:
            persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
            if persona is None or not persona.is_complete:
                raise RoomBackdropStateError("请先完成角色设定")
            row = CompanionRoomBackdrop(
                user_id=user_id,
                status=BackdropStatus.PENDING.value,
                origin=BackdropOrigin.USER_REQUEST.value,
                intent=BackdropIntent.REBUILD.value,
                outfit_fingerprint=await _current_outfit_fingerprint(db, user_id),
                source=BackdropSource.USER_UPLOAD.value,
                character_card_json=identity.model_dump_json(),
            )
        else:
            row = await db.scalar(
                select(CompanionRoomBackdrop).where(
                    CompanionRoomBackdrop.id == backdrop_id,
                    CompanionRoomBackdrop.user_id == user_id,
                ),
            )
            if row is None:
                raise RoomBackdropNotFoundError(f"backdrop {backdrop_id} not found")
        if row.status != BackdropStatus.PENDING.value or row.source != BackdropSource.USER_UPLOAD.value:
            raise RoomBackdropStateError("该房间记录不在等待上传状态")
        avatar = (
            await db.execute(
                select(AvatarAsset).where(
                    AvatarAsset.user_id == user_id,
                    AvatarAsset.active.is_(True),
                ),
            )
        ).scalar_one_or_none()
        if avatar is None or not avatar.seed_fullbody_url:
            raise RoomBackdropStateError("全身形象缺失，请在设置的“角色与记忆”中重新生成")
        seed_portrait = avatar.seed_fullbody_url
        origin = row.origin
        if backdrop_id is None:
            await _supersede_pending(db, user_id)
            db.add(row)
            await db.commit()
            backdrop_id = row.id
            _cancel_inflight_task(user_id)

    ext = {"image/gif": "gif", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
    storage_path = await asset_store.save_companion_asset_async(
        data,
        user_id=user_id,
        label="room_backdrop",
        ext=ext,
    )
    public_url = (
        asset_store.signed_companion_asset_url(storage_path)
        or f"/api/companion/asset/{user_id}/{Path(storage_path).name}"
    )
    activated = await _finalize_ready_row(
        backdrop_id,
        user_id,
        storage_path=storage_path,
        public_url=public_url,
        seed_portrait_media_id=seed_portrait,
        origin=origin,
    )
    if not activated:
        # 校验通过后到落库前行可能被新请求取代；_finalize_ready_row 已清理本轮资产，显式失败避免静默丢弃上传。
        raise RoomBackdropStateError("房间状态已变化，请刷新后重试")
    async with SESSION_LOCAL() as db:
        final_row = await get_backdrop(db, user_id, backdrop_id)
    if final_row is None:
        raise RoomBackdropNotFoundError(f"backdrop {backdrop_id} not found")
    return final_row


async def discard_room_backdrop(user_id: int, backdrop_id: int) -> CompanionRoomBackdrop:
    """放弃等待上传的自备图行（pending+user_upload → superseded）。"""
    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        row = (
            await db.execute(
                select(CompanionRoomBackdrop).where(
                    CompanionRoomBackdrop.id == backdrop_id,
                    CompanionRoomBackdrop.user_id == user_id,
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            raise RoomBackdropNotFoundError(f"backdrop {backdrop_id} not found")
        if row.status != BackdropStatus.PENDING.value or row.source != BackdropSource.USER_UPLOAD.value:
            raise RoomBackdropStateError("该房间记录不在等待上传状态")
        row.status = BackdropStatus.SUPERSEDED.value
        await db.commit()
        await db.refresh(row)
        return row


async def delete_room_backdrop(user_id: int, backdrop_id: int) -> None:
    """删除非当前 active 的 ready/failed/superseded 房间行；pending 不可删。"""
    media_path = ""
    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        row = (
            await db.execute(
                select(CompanionRoomBackdrop).where(
                    CompanionRoomBackdrop.id == backdrop_id,
                    CompanionRoomBackdrop.user_id == user_id,
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            raise RoomBackdropNotFoundError(f"backdrop {backdrop_id} not found")
        if row.status == BackdropStatus.PENDING.value:
            if row.source == BackdropSource.USER_UPLOAD.value:
                raise RoomBackdropStateError("等待上传的自备图请使用放弃操作")
            raise RoomBackdropStateError("生成中的房间不能删除，请等就绪或失败后再试")

        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is not None and persona.active_backdrop_id == row.id:
            raise RoomBackdropStateError("当前正在使用的房间不能删除，请先换回其他房间")

        if row.media_path and row.media_path.startswith("companion-assets/"):
            media_path = row.media_path
        await db.execute(delete(CompanionRoomBackdrop).where(CompanionRoomBackdrop.id == row.id))
        await db.commit()

    # 先落库再删文件：提交失败时行仍在，不会留下无资产的悬空记录。
    if media_path:
        asset_store.unlink_companion_asset(media_path)


def _reference_image_mime(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
            raise ValueError("unsupported reference image format")
        if Image.MAX_IMAGE_PIXELS is not None and image.width * image.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("reference image dimensions exceed limit")
        mime = Image.MIME[image.format]
        image.load()
        return mime


def _decode_reference_image(data: bytes) -> tuple[bytes, str]:
    """校验图片字节（体积、格式白名单、像素上限、实际解码），返回原字节与 MIME；不合法抛 ValueError。"""
    if not data or len(data) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
        raise ValueError("reference image size exceeds limit")
    return data, _reference_image_mime(data)


async def _prepare_reference_image(reference: str) -> str:
    try:
        if reference.startswith("data:"):
            header, separator, payload = reference.partition(",")
            if not separator or not header.endswith(";base64"):
                raise ValueError("invalid image data URI")
            data = await asyncio.to_thread(base64.b64decode, payload, validate=True)
        else:
            data, _ = await resolve_reference_bytes(reference)
        data, mime = await asyncio.to_thread(_decode_reference_image, data)
    except Exception as exc:
        raise RoomBackdropError("参考图无法读取，请换一张有效的 PNG / JPEG / WebP / GIF 图片") from exc
    encoded = await asyncio.to_thread(base64.b64encode, data)
    return f"data:{mime};base64,{encoded.decode('ascii')}"


async def resume_room_generation(
    user_id: int,
    backdrop_id: int,
    *,
    notes: str | None = None,
) -> bool:
    """恢复仍为 pending 且已失去进程内 task 的同一房间行，不新增生成记录。"""
    active_task = _INFLIGHT_TASKS.get(user_id)
    if active_task is not None and not active_task.done():
        return True
    async with SESSION_LOCAL() as db:
        row = (
            await db.execute(
                select(CompanionRoomBackdrop).where(
                    CompanionRoomBackdrop.id == backdrop_id,
                    CompanionRoomBackdrop.user_id == user_id,
                    CompanionRoomBackdrop.status == BackdropStatus.PENDING.value,
                ),
            )
        ).scalar_one_or_none()
    if row is None:
        return False
    if row.source == BackdropSource.USER_UPLOAD.value:
        # 等待用户回传图像的自备图行不做 AI 生成恢复，由 adopt / discard 收敛
        return True
    _launch_generation_task(
        row.id,
        user_id,
        origin=row.origin,
        intent=row.intent,
        notes=notes,
    )
    return True


async def schedule_initial_room(user_id: int) -> CompanionRoomBackdrop | None:
    """完整角色卡发布后补首次房间；已有任务或房间不重复生成。"""
    async with SESSION_LOCAL() as db:
        if await load_character_snapshot(db, user_id) is None:
            return None
        existing = await db.scalar(
            select(CompanionRoomBackdrop.id).where(CompanionRoomBackdrop.user_id == user_id).limit(1),
        )
        if existing is not None:
            return None
    try:
        return await schedule_room_generation(
            user_id,
            origin=BackdropOrigin.ONBOARDING.value,
            intent=BackdropIntent.DECORATE.value,
        )
    except RoomBackdropStateError:
        return None


def _cancel_inflight_task(user_id: int) -> None:
    """取消该用户在飞的房间生成任务；新请求取代旧任务时调用，避免旧结果无人消费仍跑完整生图。"""
    old_task = _INFLIGHT_TASKS.get(user_id)
    if old_task and not old_task.done():
        old_task.cancel()


def _launch_generation_task(
    backdrop_id: int,
    user_id: int,
    *,
    origin: str,
    intent: str,
    notes: str | None,
    reference_image: str | None = None,
) -> None:
    _cancel_inflight_task(user_id)

    async def _runner() -> None:
        try:
            await _run_pipeline(
                backdrop_id,
                user_id,
                origin=origin,
                intent=intent,
                notes=notes,
                reference_image=reference_image,
            )
        except asyncio.CancelledError:
            logger.info(
                "room backdrop pipeline cancelled by newer request",
                extra={"user_id": user_id, "backdrop_id": backdrop_id},
            )
        except Exception:
            logger.exception(
                "room backdrop pipeline crashed",
                extra={"user_id": user_id, "backdrop_id": backdrop_id},
            )
            await _mark_failed(backdrop_id, _DEFAULT_FAILURE_UTTERANCE)
        finally:
            if _INFLIGHT_TASKS.get(user_id) is asyncio.current_task():
                _INFLIGHT_TASKS.pop(user_id, None)

    task = asyncio.create_task(_runner(), name=f"companion.room.{user_id}.{backdrop_id}")
    _INFLIGHT_TASKS[user_id] = task
    track_user_task(user_id, task, cancel_on_maintenance=False)


async def _run_pipeline(
    backdrop_id: int,
    user_id: int,
    *,
    origin: str,
    intent: str,
    notes: str | None,
    reference_image: str | None = None,
) -> None:
    """brief → prompt → generate_images → 落 media → 设 active / emit ready。失败重试与 uttered 错误在内部。"""
    attempts = max(1, int(SETTINGS.room_max_attempts))
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        async with SESSION_LOCAL() as db:
            row = (
                await db.execute(
                    select(CompanionRoomBackdrop).where(
                        CompanionRoomBackdrop.id == backdrop_id,
                        CompanionRoomBackdrop.user_id == user_id,
                    ),
                )
            ).scalar_one_or_none()
            if row is None or row.status != BackdropStatus.PENDING.value:
                return
            row.attempt_count = attempt
            await db.commit()
        await _emit_backdrop_event(
            user_id,
            "companion.room.progress",
            {"backdrop_id": backdrop_id, "stage": "brief"},
        )
        brief = await _compose_brief(user_id, intent=intent, notes=notes)
        try:
            await _do_one_attempt(
                backdrop_id,
                user_id,
                brief=brief,
                intent=intent,
                notes=notes,
                reference_image=reference_image,
                origin=origin,
                attempt=attempt,
            )
            return
        except RoomBackdropStateError as exc:
            await _mark_failed(backdrop_id, str(exc))
            return
        except ImageGenerationError as exc:
            last_error = str(exc)
            logger.warning(
                "room generation attempt failed",
                extra={
                    "user_id": user_id,
                    "backdrop_id": backdrop_id,
                    "attempt": attempt,
                    "error": last_error,
                },
            )
            ROOM_BACKDROP_FAILURES_TOTAL.labels(stage="image_error").inc()
        except Exception:
            last_error = "internal error"
            logger.warning(
                "room generation attempt errored",
                extra={
                    "user_id": user_id,
                    "backdrop_id": backdrop_id,
                    "attempt": attempt,
                },
                exc_info=True,
            )
            ROOM_BACKDROP_FAILURES_TOTAL.labels(stage="store").inc()
    await _mark_failed(backdrop_id, _DEFAULT_FAILURE_UTTERANCE)


async def _compose_brief(user_id: int, *, intent: str, notes: str | None) -> str:
    """装配简短房间建议；无效输出使用默认陈设，用户原始要求仍独立传给生图模型。"""
    async with SESSION_LOCAL() as db:
        llm_cfg = await resolve_user_llm_config(db, user_id)
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        definition = load_persona_definition(persona) if persona else {}
    payload: dict[str, Any] = {
        "intent": intent,
        "personality": definition.get("personality", ""),
        "notes": notes or "",
    }
    try:
        raw = await call_llm_once(
            llm_cfg,
            ROOM_BRIEF_SYSTEM,
            payload,
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            json_output=True,
        )
    except Exception as exc:  # noqa: BLE001 - 房间简述失败统一降级本地模板，不能连坐生图
        logger.info(
            "room brief fallback to template",
            extra={"user_id": user_id, "error": str(exc)},
        )
        return _fallback_brief(intent)
    parsed = parse_llm_json(raw) or {}
    brief = (parsed.get("brief") if isinstance(parsed, dict) else None) or ""
    if not isinstance(brief, str) or not 1 <= len(brief.strip()) <= 100:
        logger.info("room brief invalid; using default suggestions", extra={"user_id": user_id})
        return _fallback_brief(intent)
    return brief.strip()


def _fallback_brief(intent: str) -> str:
    return {
        "decorate": "柔和的木质家具、几本书、一杯热茶，窗边斜阳。",
        "seasonal": "温和自然光与一两处可替换的季节装饰，整体不喧宾夺主。",
        "mood": "暖色与低饱和的灯光，留出可冥想的空间。",
        "rebuild": "明亮的起居空间，桌椅上放着几件生活小物件。",
    }.get(intent, "明亮的起居空间，桌椅上放着几件生活小物件。")


async def _do_one_attempt(
    backdrop_id: int,
    user_id: int,
    *,
    brief: str,
    intent: str,
    notes: str | None,
    origin: str,
    attempt: int,
    reference_image: str | None = None,
) -> None:
    await _emit_backdrop_event(
        user_id,
        "companion.room.progress",
        {"backdrop_id": backdrop_id, "stage": "imagine"},
    )
    ROOM_BACKDROP_IMAGES_TOTAL.labels(origin=origin, result="attempt").inc()
    async with SESSION_LOCAL() as db:
        backdrop = (
            await db.execute(
                select(CompanionRoomBackdrop).where(
                    CompanionRoomBackdrop.id == backdrop_id,
                    CompanionRoomBackdrop.user_id == user_id,
                    CompanionRoomBackdrop.status == BackdropStatus.PENDING.value,
                ),
            )
        ).scalar_one_or_none()
        if backdrop is None:
            return
        avatar = (
            await db.execute(
                select(AvatarAsset).where(
                    AvatarAsset.user_id == user_id,
                    AvatarAsset.active.is_(True),
                ),
            )
        ).scalar_one_or_none()
        outfit = (
            await db.execute(
                select(CompanionOutfit)
                .where(
                    CompanionOutfit.user_id == user_id,
                    CompanionOutfit.active.is_(True),
                    CompanionOutfit.status == "ready",
                )
                .order_by(CompanionOutfit.id.desc())
                .limit(1),
            )
        ).scalar_one_or_none()
    if avatar is None or not (identity_uri := await asyncio.to_thread(load_character_reference_data_uri, avatar)):
        raise RoomBackdropStateError("全身形象缺失或无法读取，请在设置的“角色与记忆”中重新生成")
    outfit_description = (outfit.description or "").strip() if outfit is not None else ""
    prompt = build_room_prompt(
        RoomPromptContext(
            intent=intent,
            outfit_description=outfit_description,
            brief=brief,
            notes=notes or "",
            has_reference_image=reference_image is not None,
        ),
    )
    generated_url = (
        backdrop.media_path if backdrop.media_path.startswith(("http://", "https://", "/api/media/files/")) else ""
    )
    if not generated_url:
        urls = await generate_images(
            prompt,
            size="1792x1024",
            n=1,
            user_id=user_id,
            reference_image=identity_uri,
            secondary_reference_image=reference_image,
        )
        if not urls:
            raise ImageGenerationError("empty image result")
        generated_url = urls[0]
        async with SESSION_LOCAL() as db:
            row = (
                await db.execute(
                    select(CompanionRoomBackdrop).where(
                        CompanionRoomBackdrop.id == backdrop_id,
                        CompanionRoomBackdrop.user_id == user_id,
                        CompanionRoomBackdrop.status == BackdropStatus.PENDING.value,
                    ),
                )
            ).scalar_one_or_none()
            if row is None:
                return
            # pending 行暂存供应商结果地址；进程重启只继续转存，不再次提交生图。
            row.media_path = generated_url
            row.brief = brief
            row.prompt = prompt
            await db.commit()
        log_paid_call(
            "room_backdrop",
            "image_generated",
            user_id=user_id,
            backdrop_id=backdrop_id,
            attempt=attempt,
        )
    await _emit_backdrop_event(
        user_id,
        "companion.room.progress",
        {"backdrop_id": backdrop_id, "stage": "store"},
    )

    image_bytes_result = await _fetch_image_bytes(generated_url)
    if image_bytes_result is None:
        logger.warning(
            "room generation failed to fetch image bytes",
            extra={"user_id": user_id, "backdrop_id": backdrop_id, "url": generated_url},
        )
        ROOM_BACKDROP_FAILURES_TOTAL.labels(stage="imagine").inc()
        raise ImageGenerationError("failed to retrieve image bytes")

    data, content_type = image_bytes_result
    if len(data) < _MIN_IMAGE_BYTES or not any(data.startswith(magic) for magic in _VALID_IMAGE_MAGIC):
        logger.warning(
            "room generation failed weak quality check",
            extra={"user_id": user_id, "backdrop_id": backdrop_id, "size": len(data)},
        )
        ROOM_BACKDROP_FAILURES_TOTAL.labels(stage="imagine").inc()
        raise ImageGenerationError("weak quality check failed")

    ext = {
        "image/bmp": "bmp",
        "image/gif": "gif",
        "image/png": "png",
        "image/webp": "webp",
    }.get((content_type or "").lower(), "jpg")
    storage_path = await asset_store.save_companion_asset_async(
        data,
        user_id=user_id,
        label="room_backdrop",
        ext=ext,
    )
    public_url = (
        asset_store.signed_companion_asset_url(storage_path)
        or f"/api/companion/asset/{user_id}/{Path(storage_path).name}"
    )

    should_activate = await _finalize_ready_row(
        backdrop_id,
        user_id,
        storage_path=storage_path,
        public_url=public_url,
        seed_portrait_media_id=avatar.seed_fullbody_url,
        origin=origin,
    )

    if origin == BackdropOrigin.NIGHTLY.value and should_activate:
        try:
            async with SESSION_LOCAL() as db:
                await create_user_moment(
                    db,
                    user_id,
                    title="房间布置",
                    body=brief,
                    media_url=storage_path,
                    kind=MomentKind.SCENE.value,
                    source="nightly",
                )
        except Exception:
            logger.warning(
                "failed to write nightly room moment",
                extra={"user_id": user_id},
                exc_info=True,
            )


async def _finalize_ready_row(
    backdrop_id: int,
    user_id: int,
    *,
    storage_path: str,
    public_url: str,
    seed_portrait_media_id: str,
    origin: str,
) -> bool:
    """pending 行落成 ready：写产物路径与种子锚，按换装指纹与政策决定是否激活并广播 ready；
    返回是否激活。生成链与自备图采纳共用；行已被取代或删除时清理产物并返回 False。
    brief/prompt 由调用方在生图/提示词步骤落库，此处不覆写。"""
    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        row = (
            await db.execute(
                select(CompanionRoomBackdrop).where(
                    CompanionRoomBackdrop.id == backdrop_id,
                    CompanionRoomBackdrop.user_id == user_id,
                ),
            )
        ).scalar_one_or_none()
        if row is None or row.status != BackdropStatus.PENDING.value:
            asset_store.unlink_companion_asset(storage_path)
            return False
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        row.public_url = public_url
        row.media_path = storage_path
        row.seed_portrait_media_id = seed_portrait_media_id
        row.seed_outfit_media_id = ""
        row.status = BackdropStatus.READY.value
        row.ready_at = utc_now()
        current_fingerprint = await _current_outfit_fingerprint(db, user_id)
        is_same_outfit = (
            not current_fingerprint or not row.outfit_fingerprint or row.outfit_fingerprint == current_fingerprint
        )
        same_identity = await character_snapshot_is_current(
            db,
            user_id,
            CharacterCardSnapshot.model_validate_json(row.character_card_json),
        )
        should_activate = (
            same_identity
            and is_same_outfit
            and (
                origin not in _AUTONOMOUS_ORIGINS
                or (persona is not None and persona.backdrop_policy != BackdropPolicy.LOCKED.value)
            )
        )
        if should_activate:
            if persona is None:
                persona = Persona(user_id=user_id, definition_json="{}")
                db.add(persona)
                await db.flush()
            persona.active_backdrop_id = row.id
        await db.commit()
        await db.refresh(row)
        ROOM_BACKDROP_IMAGES_TOTAL.labels(origin=origin, result="ready").inc()
        if should_activate:
            await _emit_backdrop_event(
                user_id,
                "companion.room.ready",
                _event_payload(row),
            )
    return should_activate


def _event_payload(row: CompanionRoomBackdrop) -> dict[str, Any]:
    url = row.public_url or ""
    if row.media_path and row.media_path.startswith("companion-assets/"):
        url = asset_store.signed_companion_asset_url(row.media_path) or url
    return {
        "id": row.id,
        "backdrop_id": row.id,
        "url": url,
        "brief": row.brief,
        "origin": row.origin,
        "source": row.source,
        "outfit_fingerprint": row.outfit_fingerprint,
    }


async def _mark_failed(backdrop_id: int, utterance: str) -> None:
    async with SESSION_LOCAL() as db:
        row = (
            await db.execute(
                select(CompanionRoomBackdrop).where(
                    CompanionRoomBackdrop.id == backdrop_id,
                ),
            )
        ).scalar_one_or_none()
        if row is None or row.status != BackdropStatus.PENDING.value:
            return
        row.status = BackdropStatus.FAILED.value
        if row.media_path and not row.media_path.startswith("companion-assets/"):
            row.media_path = ""
        row.error_utterance = utterance[:500]
        origin = row.origin
        await db.commit()
        ROOM_BACKDROP_IMAGES_TOTAL.labels(origin=origin, result="failed").inc()
        await _emit_backdrop_event(
            row.user_id,
            "companion.room.failed",
            {"backdrop_id": backdrop_id, "utterance": utterance},
        )


def response_for_backdrop(row: CompanionRoomBackdrop | None) -> dict[str, Any]:
    if row is None:
        return {}
    url = row.public_url or ""
    if row.media_path and row.media_path.startswith("companion-assets/"):
        url = asset_store.signed_companion_asset_url(row.media_path) or url
    return {
        "id": row.id,
        "status": row.status,
        "origin": row.origin,
        "intent": row.intent,
        "source": row.source,
        "brief": row.brief,
        "prompt": row.prompt,
        "url": url,
        "outfit_fingerprint": row.outfit_fingerprint,
        "seed_portrait_media_id": row.seed_portrait_media_id,
        "seed_outfit_media_id": row.seed_outfit_media_id,
        "error_utterance": row.error_utterance,
        "attempt_count": row.attempt_count,
        "requested_at": row.requested_at,
        "ready_at": row.ready_at,
    }


async def drain_room_backdrop_jobs() -> None:
    """取消并等待所有后台房间图生成任务完成。"""
    tasks = list(_INFLIGHT_TASKS.values())
    if not tasks:
        return
    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _INFLIGHT_TASKS.clear()


async def _fetch_image_bytes(url: str) -> tuple[bytes, str] | None:
    """把生成结果 URL 解析为 (bytes, content_type)，不可达时返回 None。"""
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?", maxsplit=1)[0]
        res = get_file_path(fid)
        if res:
            path, ctype = res
            return await asyncio.to_thread(Path(path).read_bytes), ctype
    try:
        content = await download_capped(url, max_bytes=ROOM_BACKDROP_DOWNLOAD_MAX_BYTES, timeout=120.0)
        if content:
            return content, _image_content_type(content)
    except Exception:
        logger.warning("failed to fetch image bytes", extra={"url": url}, exc_info=True)
    return None


def _image_content_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "application/octet-stream"
