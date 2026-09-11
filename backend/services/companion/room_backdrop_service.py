"""伙伴房间图生命周期。

触发点：onboarding 形象确认 / 换装成功 / 用户 HTTP 换房 / 在线 LLM 工具换房 / 夜间自主规划。
同 persona 同时只允许一个 pending（CAS），新请求把旧 pending 标 superseded。
ready 行同步设 active（除非政策在自主生成期间被锁定）；保留最近 5 张 ready 供回滚。
故障人格化：失败后写 error_utterance 给 Client 朗读；最多 3 次尝试。
"""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any, TypedDict

from components import (
    ROOM_BACKDROP_FAILURES_TOTAL,
    ROOM_BACKDROP_IMAGES_TOTAL,
    SESSION_LOCAL,
    SETTINGS,
    download_capped,
    get_file_path,
    get_logger,
    log_paid_call,
    parse_llm_json,
    utc_now,
)
from components.user_maintenance_runtime import track_user_task
from modules.companion import (
    AvatarAsset,
    BackdropIntent,
    BackdropOrigin,
    BackdropPolicy,
    BackdropStatus,
    CompanionOutfit,
    CompanionRoomBackdrop,
    MomentKind,
    Persona,
)
from modules.ws import emit_ws_event
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import call_llm_once, resolve_user_llm_config
from services.media import ImageGenerationError, generate_images

from . import asset_store
from .avatar_service import (
    load_avatar_bytes_as_data_uri,
)
from .journal_service import create_user_moment, write_system_moment
from .persona_service import load_persona_definition
from .room_prompt import RoomPromptContext, build_room_prompt

logger = get_logger(__name__)

# 进程内每用户锁：生图与 activate 走同一锁防并发把同一 persona 的 active 状态撞出多行。
_BACKDROP_LOCKS: dict[int, asyncio.Lock] = {}
# 进程内任务表，便于 lifespan 重启时接续（与 3D 管线同思路；规模小、本期不接入启动恢复）。
_INFLIGHT_TASKS: dict[int, asyncio.Task[None]] = {}

_DEFAULT_FAILURE_UTTERANCE = "房间还没收拾完，你先坐一会儿。"
_ROOM_BRIEF_SYSTEM = '你是桌面伙伴的房间内饰设计师。根据用户画像与季节 / 心情 / 装饰意图，给出一段不超过 80 字的中文房间简述（不写五官、衣着细节）。只输出一个 JSON：{"brief": "..."}'
_ONE_DAY = timedelta(days=1)
_AUTONOMOUS_ORIGINS = frozenset(
    (BackdropOrigin.LLM.value, BackdropOrigin.NIGHTLY.value),
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


async def list_backdrops(db: AsyncSession, user_id: int) -> list[CompanionRoomBackdrop]:
    return (
        (
            await db.execute(
                select(CompanionRoomBackdrop)
                .where(CompanionRoomBackdrop.user_id == user_id)
                .order_by(CompanionRoomBackdrop.id.desc()),
            )
        )
        .scalars()
        .all()
    )


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


class RoomStateDict(TypedDict):
    active: CompanionRoomBackdrop | None
    history: list[CompanionRoomBackdrop]
    policy: str
    pending: CompanionRoomBackdrop | None


async def get_room_state(db: AsyncSession, user_id: int) -> RoomStateDict:
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
                )
                .limit(SETTINGS.room_history_keep),
            )
        )
        .scalars()
        .all()
    )
    return {
        "active": active,
        "history": list(history_rows),
        "policy": (persona.backdrop_policy if persona is not None else BackdropPolicy.LLM_MAY_REPLACE.value),
        "pending": pending,
    }


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
            _event_payload(target, persona),
        )
        return target


async def invalidate_room_for_outfit(user_id: int, new_fingerprint: str | None) -> None:
    """换装成功后调用：把当前 active 行标 superseded 并 schedule origin=outfit 的重建。"""
    should_rebuild = False
    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete:
            return
        active_id = persona.active_backdrop_id
        if active_id is not None:
            active = (
                await db.execute(
                    select(CompanionRoomBackdrop).where(
                        CompanionRoomBackdrop.id == active_id,
                        CompanionRoomBackdrop.user_id == user_id,
                    ),
                )
            ).scalar_one_or_none()
            if active is not None and (not new_fingerprint or active.outfit_fingerprint == new_fingerprint):
                return
            if active is not None:
                active.status = BackdropStatus.SUPERSEDED.value
            persona.active_backdrop_id = None
            await db.commit()
            should_rebuild = True
            await _emit_backdrop_event(
                user_id,
                "companion.room.invalidated",
                {"reason": "outfit", "active_backdrop_id": active_id},
            )
        else:
            pending = await get_pending_backdrop(db, user_id)
            if pending is not None and (not new_fingerprint or pending.outfit_fingerprint == new_fingerprint):
                return
            has_backdrop = (
                await db.execute(
                    select(CompanionRoomBackdrop.id).where(CompanionRoomBackdrop.user_id == user_id).limit(1),
                )
            ).scalar_one_or_none()
            if has_backdrop is not None:
                should_rebuild = True

    if should_rebuild:
        try:
            await write_system_moment(
                user_id=user_id,
                kind=MomentKind.MILESTONE.value,
                event_key="milestone_outfit",
            )
        except Exception:
            logger.warning(
                "failed to write outfit milestone moment",
                extra={"user_id": user_id},
                exc_info=True,
            )
        await schedule_room_generation(
            user_id,
            origin=BackdropOrigin.OUTFIT.value,
            intent=BackdropIntent.REBUILD.value,
            notes=None,
        )


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


async def _trim_history(db: AsyncSession, user_id: int) -> None:
    """保留最近 N 张 ready（不淘汰当前 active）；更早的标 superseded 并从 companion-assets 物理清理。"""
    keep = int(SETTINGS.room_history_keep)
    if keep <= 0:
        return
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    active_id = persona.active_backdrop_id if persona is not None else None

    stmt = select(CompanionRoomBackdrop).where(
        CompanionRoomBackdrop.user_id == user_id,
        CompanionRoomBackdrop.status == BackdropStatus.READY.value,
    )
    if active_id is not None:
        stmt = stmt.where(CompanionRoomBackdrop.id != active_id)
    stmt = stmt.order_by(
        CompanionRoomBackdrop.ready_at.desc().nullslast(),
        CompanionRoomBackdrop.id.desc(),
    ).offset(keep)
    stale_rows = list((await db.execute(stmt)).scalars().all())
    if not stale_rows:
        return
    stale_ids = [r.id for r in stale_rows]
    await db.execute(
        update(CompanionRoomBackdrop)
        .where(CompanionRoomBackdrop.id.in_(stale_ids))
        .values(status=BackdropStatus.SUPERSEDED.value),
    )
    for r in stale_rows:
        if r.media_path and r.media_path.startswith("companion-assets/"):
            asset_store.unlink_companion_asset(r.media_path)


async def schedule_room_generation(
    user_id: int,
    *,
    origin: str,
    intent: str = "rebuild",
    notes: str | None = None,
) -> CompanionRoomBackdrop:
    """创建 pending 行；返回行对象供 HTTP 端点返回 202。后续生图走 fire-and-forget。"""
    async with _backdrop_lock(user_id), SESSION_LOCAL() as db:
        if origin == BackdropOrigin.LLM.value:
            await _consume_llm_quota(db, user_id)
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete:
            raise RoomBackdropStateError("persona not ready; complete onboarding first")
        if origin in _AUTONOMOUS_ORIGINS and persona.backdrop_policy == BackdropPolicy.LOCKED.value:
            raise RoomBackdropLockedError("房间已被你锁住，想换就解开再说。")
        await _supersede_pending(db, user_id)
        outfit_fingerprint = await _current_outfit_fingerprint(db, user_id)
        row = CompanionRoomBackdrop(
            user_id=user_id,
            status=BackdropStatus.PENDING.value,
            origin=origin,
            intent=intent,
            outfit_fingerprint=outfit_fingerprint,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    _launch_generation_task(row.id, user_id, origin=origin, intent=intent, notes=notes)
    return row


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
    _launch_generation_task(
        row.id,
        user_id,
        origin=row.origin,
        intent=row.intent,
        notes=notes,
    )
    return True


async def schedule_initial_room(user_id: int) -> CompanionRoomBackdrop | None:
    """onboarding 形象确认后调用；与 2D/3D 资产生成并行，不挡问候。"""
    try:
        return await schedule_room_generation(
            user_id,
            origin=BackdropOrigin.ONBOARDING.value,
            intent=BackdropIntent.DECORATE.value,
        )
    except RoomBackdropStateError:
        return None


def _launch_generation_task(
    backdrop_id: int,
    user_id: int,
    *,
    origin: str,
    intent: str,
    notes: str | None,
) -> None:
    old_task = _INFLIGHT_TASKS.get(user_id)
    if old_task and not old_task.done():
        old_task.cancel()

    async def _runner() -> None:
        try:
            await _run_pipeline(
                backdrop_id,
                user_id,
                origin=origin,
                intent=intent,
                notes=notes,
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
                origin=origin,
                attempt=attempt,
            )
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
    """便宜 LLM 装配的房间简述（≤ 80 字）；失败时降级为静态模板。"""
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
            _ROOM_BRIEF_SYSTEM,
            payload,
            max_output_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001 - 房间简述失败统一降级本地模板，不能连坐生图
        logger.info(
            "room brief fallback to template",
            extra={"user_id": user_id, "error": str(exc)},
        )
        return _fallback_brief(intent, notes)
    parsed = parse_llm_json(raw) or {}
    brief = (parsed.get("brief") if isinstance(parsed, dict) else None) or ""
    if not brief.strip():
        return _fallback_brief(intent, notes)
    return brief.strip()[:200]


def _fallback_brief(intent: str, notes: str | None) -> str:
    base = {
        "decorate": "柔和的木质家具、几本书、一杯热茶，窗边斜阳。",
        "seasonal": "四季的氛围元素融入房间，整体不喧宾夺主。",
        "mood": "暖色与低饱和的灯光，留出可冥想的空间。",
        "rebuild": "明亮的起居空间，桌椅上放着几件生活小物件。",
    }.get(intent, "明亮的起居空间，桌椅上放着几件生活小物件。")
    if notes:
        return f"{base} {notes.strip()[:80]}"
    return base


async def _do_one_attempt(
    backdrop_id: int,
    user_id: int,
    *,
    brief: str,
    intent: str,
    notes: str | None,
    origin: str,
    attempt: int,
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
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
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
    definition = load_persona_definition(persona) if persona else {}
    identity_uri: str | None = None
    if avatar is not None:
        identity_uri = load_avatar_bytes_as_data_uri(avatar.asset_url)
    outfit_uri = load_avatar_bytes_as_data_uri(outfit.fullbody_url) if outfit is not None else None
    outfit_description = (outfit.description or "").strip() if outfit is not None else ""
    prompt = build_room_prompt(
        RoomPromptContext(
            species=definition.get("biological_type", ""),
            appearance=definition.get("appearance", ""),
            intent=intent,
            has_identity_ref=bool(identity_uri),
            has_outfit_ref=bool(outfit_uri),
            outfit_description=outfit_description,
            brief=brief,
            notes=notes or "",
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
            secondary_reference_image=outfit_uri,
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
    storage_path = asset_store.save_companion_asset(
        data,
        user_id=user_id,
        label="room_backdrop",
        ext=ext,
    )
    public_url = (
        asset_store.signed_companion_asset_url(storage_path)
        or f"/api/companion/asset/{user_id}/{Path(storage_path).name}"
    )

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
            return
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        row.brief = brief
        row.prompt = prompt
        row.public_url = public_url
        row.media_path = storage_path
        row.seed_portrait_media_id = (avatar.asset_url or "") if avatar else ""
        row.seed_outfit_media_id = outfit.fullbody_url if outfit else (avatar.seed_front_2d_url if avatar else "")
        row.status = BackdropStatus.READY.value
        row.ready_at = utc_now()
        current_fingerprint = await _current_outfit_fingerprint(db, user_id)
        is_same_outfit = (
            not current_fingerprint or not row.outfit_fingerprint or row.outfit_fingerprint == current_fingerprint
        )
        should_activate = is_same_outfit and (
            origin not in _AUTONOMOUS_ORIGINS
            or (persona is not None and persona.backdrop_policy != BackdropPolicy.LOCKED.value)
        )
        if should_activate:
            if persona is None:
                persona = Persona(user_id=user_id, definition_json="{}")
                db.add(persona)
                await db.flush()
            persona.active_backdrop_id = row.id
        await _trim_history(db, user_id)
        await db.commit()
        await db.refresh(row)
        ROOM_BACKDROP_IMAGES_TOTAL.labels(origin=origin, result="ready").inc()
        if should_activate:
            await _emit_backdrop_event(
                user_id,
                "companion.room.ready",
                _event_payload(row, persona),
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
    elif origin in (BackdropOrigin.USER_REQUEST.value, BackdropOrigin.LLM.value):
        try:
            await write_system_moment(
                user_id=user_id,
                kind=MomentKind.SCENE.value,
                event_key="scene_room",
                media_url=storage_path,
            )
        except Exception:
            logger.warning(
                "failed to write scene moment for room",
                extra={"user_id": user_id},
                exc_info=True,
            )


def _event_payload(
    row: CompanionRoomBackdrop,
    persona: Persona | None,
) -> dict[str, Any]:
    url = row.public_url or ""
    if row.media_path and row.media_path.startswith("companion-assets/"):
        url = asset_store.signed_companion_asset_url(row.media_path) or url
    return {
        "id": row.id,
        "backdrop_id": row.id,
        "url": url,
        "brief": row.brief,
        "origin": row.origin,
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


_MIN_IMAGE_BYTES: int = 4 * 1024
_VALID_IMAGE_MAGIC: tuple[bytes, ...] = (
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"RIFF",
    b"GIF8",
    b"BM",
)


async def _fetch_image_bytes(url: str) -> tuple[bytes, str] | None:
    """把生成结果 URL 解析为 (bytes, content_type)，不可达时返回 None。"""
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?", maxsplit=1)[0]
        res = get_file_path(fid)
        if res:
            path, ctype = res
            return Path(path).read_bytes(), ctype
    try:
        content = await download_capped(url, max_bytes=20 * 1024 * 1024, timeout=120.0)
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
