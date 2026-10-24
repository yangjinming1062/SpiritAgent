"""伙伴影像与动作素材的跨业务编排：组合冻结、模型工具与评审调度。

组合在本层冻结为不可变快照，各业务模块只经公开查询和 DTO 协作；登记动作素材本身不启动制作。
"""

import asyncio
import hashlib
from typing import Any

from features.action_library import queries as action_queries
from features.action_library import service as action_service
from features.action_library.review import schedule_action_review
from features.action_library.schemas import (
    ActionAssetDesign,
    ActionAssetSource,
    ActionAssetUpdate,
    ActionAssetView,
    ActionClipKind,
    ActionReviewSource,
)
from features.appearance.character_card import (
    CharacterCardNotReadyError,
    require_character_snapshot,
)
from features.appearance.queries import avatar_snapshot, outfit_identity_matches, ready_outfit_snapshot
from features.companion.disturbance import get_disturbance_tier
from features.companion.persona_service import load_persona_definition
from features.companion.proactive_runtime import get_user_proactive_record
from features.companion.visual_state import persona_snapshot
from features.life.schemas import LifeStepRequest
from features.life.use_cases import read_state
from features.scenes.queries import scene_snapshot
from features.visuals import service as visuals_service
from features.visuals.queries import presentation_state
from features.visuals.schemas import (
    ActionMaterialRef,
    ComboMaterialRef,
    ComboSnapshot,
    VisualMediaKind,
)
from infrastructure.assets.asset_store import compute_file_sha256, resolve_asset_reference
from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.database import SESSION_LOCAL
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_TARGET = (1920, 1080)


class ComboAssemblyError(ValueError):
    """组合冻结缺少可用素材或呈现上下文。"""


def _asset_hash(path: str) -> str:
    resolved = resolve_asset_reference(path)
    if resolved is None:
        raise ComboAssemblyError("所选图片尚未准备好")
    return compute_file_sha256(resolved[0])


async def load_combo_snapshot(
    db: AsyncSession,
    user_id: int,
    *,
    outfit_id: int,
    scene_id: int,
    action_id: int,
    requirements: str = "",
    target_width: int | None = None,
    target_height: int | None = None,
) -> ComboSnapshot:
    """冻结伙伴身份与衣柜、环境、动作三份素材的引用及描述。"""

    try:
        identity = await require_character_snapshot(db, user_id)
    except CharacterCardNotReadyError as exc:
        raise ComboAssemblyError(str(exc)) from exc
    avatar = await avatar_snapshot(db, user_id, identity.avatar_id)
    persona = await persona_snapshot(db, user_id)
    if avatar is None or not avatar.seed_fullbody_url or not avatar.asset_url:
        raise ComboAssemblyError("伙伴的形象尚未准备好")
    outfit = await ready_outfit_snapshot(db, user_id, outfit_id)
    scene = await scene_snapshot(db, user_id, scene_id)
    action = await action_queries.action_detail(db, user_id, action_id)
    if outfit is None or not outfit.fullbody_url:
        raise ComboAssemblyError("请选择已就绪的造型")
    if scene is None or scene.status != "ready" or not scene.media_path:
        raise ComboAssemblyError("请选择已就绪的环境图片")
    if action is None or action.status != "approved":
        raise ComboAssemblyError("请选择已经通过评审的动作")
    identity_hash, portrait_hash, outfit_hash, scene_hash = await asyncio.gather(
        asyncio.to_thread(_asset_hash, avatar.seed_fullbody_url),
        asyncio.to_thread(_asset_hash, avatar.asset_url),
        asyncio.to_thread(_asset_hash, outfit.fullbody_url),
        asyncio.to_thread(_asset_hash, scene.media_path),
    )
    width, height = target_width, target_height
    if not width or not height:
        width, height = DEFAULT_TARGET
    return ComboSnapshot(
        identity=identity,
        identity_path=avatar.seed_fullbody_url,
        identity_hash=identity_hash,
        portrait_path=avatar.asset_url,
        portrait_hash=portrait_hash,
        identity_reference_role=(
            "portrait" if outfit_identity_matches(outfit.source_json, avatar.seed_fullbody_url) else "fullbody"
        ),
        outfit=ComboMaterialRef(
            material_id=outfit.id,
            name=outfit.name,
            path=outfit.fullbody_url,
            content_hash=outfit_hash,
            description=outfit.description or "",
            revision=outfit.updated_at,
        ),
        scene=ComboMaterialRef(
            material_id=scene.id,
            name=scene.title,
            path=scene.media_path,
            content_hash=scene_hash,
            description=scene.description,
            revision=scene.updated_at,
        ),
        action=ActionMaterialRef(
            action_id=action.id,
            revision=action.updated_at,
            name=action.name,
            motion_description=action.motion_description,
            clip_kind=action.clip_kind,
            duration_seconds=action.duration_seconds,
            use_when=action.use_when,
            avoid_when=action.avoid_when,
            allowed_triggers=action.allowed_triggers,
        ),
        plan=None,
        target_width=width,
        target_height=height,
        requirements=requirements,
        persona=load_persona_definition(persona) if persona is not None else {},
    )


async def design_action_reference(
    user_id: int,
    *,
    name: str,
    motion_description: str,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    allowed_triggers: list[str] | None = None,
    reason: str = "",
    duration_seconds: int = 4,
    clip_kind: ActionClipKind = "once",
    source: ActionAssetSource = "autonomous",
) -> dict[str, Any]:
    """登记动作素材并安排语义评审；不创建图片或视频制作。"""

    design = ActionAssetDesign(
        name=name,
        motion_description=motion_description,
        use_when=use_when or [],
        avoid_when=avoid_when or [],
        allowed_triggers=allowed_triggers or [],
        clip_kind=clip_kind,
        duration_seconds=duration_seconds,
        reason=reason,
    )
    async with SESSION_LOCAL() as db:
        review_source = await load_action_review_source(db, user_id)
        view = await action_service.create_action(
            db,
            user_id,
            design,
            source=source,
            review_source_json=review_source.model_dump_json(),
        )
        await db.commit()
    if view.status == "pending":
        schedule_action_review(user_id, view.id)
    messages = {
        "pending": "已登记，语义评审进行中",
        "reviewing": "相同动作正在评审",
        "approved": "复用已通过的相同动作",
        "deferred": "相同动作评审暂缓，可稍后重试",
        "rejected": "相同动作已被拒绝，调整定义后重新提交",
    }
    return {"action_id": view.id, "name": view.name, "status": view.status, "message": messages[view.status]}


async def load_action_review_source(db: AsyncSession, user_id: int) -> ActionReviewSource:
    identity = await require_character_snapshot(db, user_id)
    avatar = await avatar_snapshot(db, user_id, identity.avatar_id)
    persona = await persona_snapshot(db, user_id)
    if avatar is None or not avatar.seed_fullbody_url or not avatar.asset_url:
        raise ComboAssemblyError("伙伴的形象尚未准备好")
    identity_hash, portrait_hash = await asyncio.gather(
        asyncio.to_thread(_asset_hash, avatar.seed_fullbody_url),
        asyncio.to_thread(_asset_hash, avatar.asset_url),
    )
    return ActionReviewSource(
        identity_path=avatar.seed_fullbody_url,
        identity_hash=identity_hash,
        portrait_path=avatar.asset_url,
        portrait_hash=portrait_hash,
        identity=identity,
        persona=load_persona_definition(persona) if persona is not None else {},
    )


async def update_action_reference(user_id: int, action_id: int, body: ActionAssetUpdate) -> ActionAssetView | None:
    async with SESSION_LOCAL() as db:
        source = await load_action_review_source(db, user_id)
        view = await action_service.update_action(
            db,
            user_id,
            action_id,
            body,
            expected_updated_at=body.expected_updated_at,
            review_source_json=source.model_dump_json(),
        )
        await db.commit()
    if view is not None:
        schedule_action_review(user_id, action_id)
    return view


async def generate_visual_reference(
    user_id: int,
    *,
    request_id: str,
    outfit_id: int,
    scene_id: int,
    action_id: int,
    media_kind: VisualMediaKind = "image",
    requirements: str = "",
    feedback: str = "",
    video_feedback: str = "",
) -> dict[str, Any]:
    """冻结本次组合并提交制作工作项；空影像库也能开始第一次制作。"""

    async with user_asset_lock(user_id):
        state = await presentation_state(user_id)
        if not state.autonomous_enabled:
            raise ComboAssemblyError("伙伴自主制作已关闭")
        async with SESSION_LOCAL() as db:
            combo = await load_combo_snapshot(
                db,
                user_id,
                outfit_id=outfit_id,
                scene_id=scene_id,
                action_id=action_id,
                requirements=requirements,
                target_width=state.target_width,
                target_height=state.target_height,
            )
            await db.commit()
        request_key = "life:visual:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        life_state = await read_state(user_id)
        request = LifeStepRequest(
            idempotency_key=request_key,
            kind="visual.generate",
            description=f"制作伙伴影像：{combo.action.name}",
            pursuit_id=life_state.active_pursuit_id,
        )
        job = await visuals_service.create_visual_job(
            user_id,
            combo=combo,
            target_kind=media_kind,
            source="autonomous",
            feedback=feedback,
            video_feedback=video_feedback,
            request_id=request_key,
            life_step=request,
        )
        return {
            "job_id": job.id,
            "state": job.state,
            "target_kind": job.target_kind,
            "message": "制作已受理",
        }


async def play_visual_reference(
    user_id: int,
    visual_id: int,
    *,
    reason: str = "",
    proactive_turn: bool = False,
) -> dict[str, Any]:
    """请求呈现已有影像；受理不等于已播放，实际回执才更新呈现事实。"""

    if proactive_turn:
        record = get_user_proactive_record(user_id)
        if record.busy_count or await get_disturbance_tier(user_id) != "autonomous":
            return {"status": "blocked", "message": "当前活动或打扰档位不适合自主呈现"}
    command = await visuals_service.play_visual(user_id, visual_id, source="autonomous")
    return {
        "play_id": command.play_id,
        "visual_id": command.visual_id,
        "media_kind": command.media_kind,
        "status": "queued",
        "message": "已请求呈现，实际播放以桌面回执为准",
    }
