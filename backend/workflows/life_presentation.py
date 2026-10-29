"""生活情境投影：把有意义的生活变化投影为舞台意图，并兜底可显示的画面。

后端只产出 Stage Intent 与持久目标（组合或静态），不逐次决定播放；
本地常驻轮换与互动演出由客户端导演消费呈现目录完成。视觉选择不再依赖主动档位，
分别检查自主行为权限（仅自主播放/制作）与动态表现设置（motion off 时回落静态目标）。
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from features.appearance.character_card import CharacterCardNotReadyError, require_character_snapshot
from features.appearance.queries import avatar_snapshot, initial_outfit_snapshot
from features.companion.motion import get_motion_level
from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.life.queries import active_pursuit_title
from features.life.schemas import LifeStateView
from features.life.use_cases import mark_presentation_evaluated, pending_presentation_users, read_state
from features.scenes.snapshots import list_scene_snapshots
from features.visuals.queries import actual_presentation_snapshot, presentation_state
from features.visuals.schemas import (
    ComboMaterialRef,
    SceneMediaRef,
    StageIntent,
    StagePresentation,
)
from features.visuals.static_presentation import present_static
from features.visuals.static_presentation import record_stage_intent as persist_intent
from infrastructure.assets.asset_store import compute_file_sha256, resolve_asset_reference
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from runtime.user_maintenance import user_request_scope

_LOCKS: dict[int, asyncio.Lock] = {}
_RETRY_AT: dict[int, datetime] = {}
logger = get_logger(__name__)

# 结构化活动类别到建议视觉标签的确定性映射。
# intent 性质（仅计划）不驱动活动演出；resting 显式休息，其余按活动语义展开。
_TYPE_TAGS: dict[str, list[str]] = {
    "idle": ["idle"],
    "reading": ["reading", "working"],
    "working": ["working"],
    "creating": ["creating", "working"],
    "thinking": ["thinking"],
    "resting": ["resting"],
    "organizing": ["organizing", "working"],
    "socializing": ["socializing", "interaction"],
}


def _hash(path: str) -> str:
    resolved = resolve_asset_reference(path)
    if resolved is None:
        raise ValueError("静态呈现素材尚未就绪")
    return compute_file_sha256(resolved[0])


def _unavailable_snapshot(epoch: int, revision: int) -> StagePresentation:
    return StagePresentation(
        request_id=uuid4().hex,
        presentation_epoch=epoch,
        state_revision=0,
        life_revision=revision,
        error="造型与环境尚未就绪",
    )


async def _fresh_static_snapshot(user_id: int, epoch: int, revision: int) -> StagePresentation:
    """从当前素材重新冻结静态目标；仅生活初次投影或素材缺失时使用。"""

    actual = await actual_presentation_snapshot(user_id)
    if (
        actual.outfit_path
        and actual.outfit_hash
        and actual.scene_path
        and actual.scene_hash
        and actual.identity_path
        and actual.identity_hash
    ):
        return StagePresentation(
            request_id=uuid4().hex,
            presentation_epoch=epoch,
            state_revision=0,
            life_revision=revision,
            outfit=ComboMaterialRef(
                path=actual.outfit_path,
                content_hash=actual.outfit_hash,
                description=actual.outfit_description,
            ),
            scene=SceneMediaRef(
                path=actual.scene_path,
                content_hash=actual.scene_hash,
                description=actual.scene_description,
            ),
            identity_path=actual.identity_path,
            identity_hash=actual.identity_hash,
        )

    async with SESSION_LOCAL() as db:
        outfit = await initial_outfit_snapshot(db, user_id)
        scenes = (await list_scene_snapshots(db, user_id, ready_only=True, limit=1)).scenes
        try:
            identity = await require_character_snapshot(db, user_id)
        except CharacterCardNotReadyError:
            return _unavailable_snapshot(epoch, revision)
        avatar = await avatar_snapshot(db, user_id, identity.avatar_id)
    if outfit is None or not scenes or avatar is None or not avatar.seed_fullbody_url:
        return _unavailable_snapshot(epoch, revision)
    scene = scenes[0]
    if not outfit.fullbody_url or not scene.media_path:
        return _unavailable_snapshot(epoch, revision)
    outfit_hash, scene_hash, identity_hash = await asyncio.gather(
        *(asyncio.to_thread(_hash, path) for path in (outfit.fullbody_url, scene.media_path, avatar.seed_fullbody_url))
    )
    return StagePresentation(
        request_id=uuid4().hex,
        presentation_epoch=epoch,
        state_revision=0,
        life_revision=revision,
        outfit=ComboMaterialRef(
            material_id=outfit.id,
            name=outfit.name,
            path=outfit.fullbody_url,
            content_hash=outfit_hash,
            description=outfit.description or "",
        ),
        scene=SceneMediaRef(
            scene_id=scene.id,
            title=scene.title,
            path=scene.media_path,
            content_hash=scene_hash,
            description=scene.description,
        ),
        identity_path=avatar.seed_fullbody_url,
        identity_hash=identity_hash,
    )


async def _evaluate_life_presentation(user_id: int, *, force: bool = False) -> None:
    async with user_request_scope(user_id) as acquired:
        if not acquired:
            return
        async with _LOCKS.setdefault(user_id, asyncio.Lock()):
            presence = get_presentation_snapshot(user_id)
            record = get_user_proactive_record(user_id)
            if presence is None or not record.available:
                return
            # 需要 has_media 判断，保留目录装配。
            projection = await presentation_state(user_id)
            async with SESSION_LOCAL() as db:
                pending = user_id in await pending_presentation_users(db, user_id=user_id)
                motion = await get_motion_level(user_id, db=db)
            stale_static = (
                projection.static_presentation is not None
                and projection.static_presentation.presentation_epoch != presence.revision
            )
            retry_static = (
                projection.static_presentation is not None
                and projection.static_presentation.retry_at is not None
                and projection.static_presentation.retry_at <= utc_now()
            )
            if not force and not pending and not stale_static and not retry_static:
                return
            life = await read_state(user_id)
            if (
                get_presentation_snapshot(user_id) != presence
                or (await read_state(user_id)).presentation_revision != life.presentation_revision
            ):
                return
            # 动态表现关闭或没有可播媒体时，生活变化只落到静态目标；有媒体时保留组合由导演解释意图。
            # 已有冻结素材直接沿用到新代次（换新请求身份），仅缺失时才重新冻结。
            composition_media = projection.composition.media if projection.composition is not None else None
            has_media = bool(projection.ambient_catalog) or bool(
                composition_media
                and (
                    composition_media.actor is not None
                    or composition_media.scene is not None
                    or composition_media.cinematic is not None
                )
            )
            if motion == "off" or not has_media:
                previous = projection.static_presentation
                if previous is not None and previous.outfit is not None and previous.scene is not None:
                    snapshot = previous.model_copy(
                        update={
                            "request_id": uuid4().hex,
                            "presentation_epoch": presence.revision,
                            "life_revision": life.presentation_revision,
                            "receipt_kind": None,
                            "retry_at": None,
                            "error": None,
                        }
                    )
                else:
                    snapshot = await _fresh_static_snapshot(user_id, presence.revision, life.presentation_revision)
                if not await present_static(user_id, snapshot, expected_state_revision=projection.state_revision):
                    return
            await record_stage_intent(user_id, life)
            await mark_presentation_evaluated(user_id, life.presentation_revision)


async def record_stage_intent(user_id: int, life: LifeStateView) -> None:
    """把有意义的生活变化投影为舞台意图；呈现系统只读取，不改生活事实。"""

    scene_title = life.current_scene.get("title", "") if isinstance(life.current_scene, dict) else ""
    # 计划性意图不能驱动“正在做”的演出；事实性质保留在 activity_kind 供消费方判断。
    activity_type = life.activity_type if life.activity_kind != "intent" else "idle"
    async with SESSION_LOCAL() as db:
        intent = StageIntent(
            activity=life.current_activity or "",
            scene_title=scene_title,
            mood=life.mood or "",
            focus=await active_pursuit_title(db, user_id, life.active_pursuit_id),
            activity_kind=life.activity_kind or "",
            activity_type=activity_type,
            scene_tags=life.scene_tags,
            visual_tags=_TYPE_TAGS.get(activity_type, []),
            life_revision=life.presentation_revision,
        )
        await persist_intent(db, user_id, intent)
        await db.commit()


async def life_presentation_requested(user_id: int, _payload: dict) -> None:
    await evaluate_life_presentation(user_id, force=_payload.get("force") is True)


async def evaluate_life_presentation(user_id: int, *, force: bool = False) -> None:
    if not force and _RETRY_AT.get(user_id, utc_now()) > utc_now():
        return
    try:
        await _evaluate_life_presentation(user_id, force=force)
        _RETRY_AT.pop(user_id, None)
    except Exception:
        _RETRY_AT[user_id] = utc_now() + timedelta(minutes=15)
        logger.warning("life presentation review failed; retry deferred", extra={"user_id": user_id}, exc_info=True)
        raise
