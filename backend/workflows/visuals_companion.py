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
    ActionReviewRequest,
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
from features.media.visual_semantics import ActionMotionIntensity, ActionPerformanceRole, SemanticTag
from features.scenes.queries import scene_snapshot
from features.visuals import service as visuals_service
from features.visuals.queries import creation_request, presentation_state
from features.visuals.schemas import (
    ActionMaterialRef,
    ActorClipSource,
    CinematicSource,
    ComboMaterialRef,
    SceneMediaRef,
    SceneMediaSource,
    VisualAssetKind,
    VisualJobView,
    VisualMediaKind,
    VisualSnapshot,
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


async def load_visual_snapshot(
    db: AsyncSession,
    user_id: int,
    *,
    asset_kind: VisualAssetKind = "cinematic",
    outfit_id: int,
    scene_id: int | None,
    action_id: int,
    requirements: str = "",
    target_width: int | None = None,
    target_height: int | None = None,
) -> VisualSnapshot:
    """冻结一次制作的输入：actor 绑定身份、造型与动作；cinematic 另含环境。

    actor 的 `scene_id` 被忽略——透明主体动画不绑定环境。
    """

    if asset_kind not in ("actor", "cinematic"):
        raise ComboAssemblyError("影像库当前只受理主体动画与完整场景影像的制作")
    try:
        identity = await require_character_snapshot(db, user_id)
    except CharacterCardNotReadyError as exc:
        raise ComboAssemblyError(str(exc)) from exc
    avatar = await avatar_snapshot(db, user_id, identity.avatar_id)
    persona = await persona_snapshot(db, user_id)
    if avatar is None or not avatar.seed_fullbody_url or not avatar.asset_url:
        raise ComboAssemblyError("伙伴的形象尚未准备好")
    outfit = await ready_outfit_snapshot(db, user_id, outfit_id)
    action = await action_queries.action_detail(db, user_id, action_id)
    if outfit is None or not outfit.fullbody_url:
        raise ComboAssemblyError("请选择已就绪的造型")
    if action is None or action.status != "approved":
        raise ComboAssemblyError("请选择已经通过评审的动作")
    scene = await scene_snapshot(db, user_id, scene_id) if scene_id is not None else None
    if asset_kind == "cinematic" and (scene is None or scene.status != "ready" or not scene.media_path):
        raise ComboAssemblyError("请选择已就绪的环境图片")
    hash_paths = [avatar.seed_fullbody_url, avatar.asset_url, outfit.fullbody_url]
    if scene is not None:
        hash_paths.append(scene.media_path)
    hashes = await asyncio.gather(*(asyncio.to_thread(_asset_hash, path) for path in hash_paths))
    identity_hash, portrait_hash, outfit_hash = hashes[0], hashes[1], hashes[2]
    scene_hash = hashes[3] if scene is not None else None
    width, height = target_width, target_height
    if not width or not height:
        width, height = DEFAULT_TARGET
    persona_definition = load_persona_definition(persona) if persona is not None else {}
    shared = {
        "identity": identity,
        "identity_path": avatar.seed_fullbody_url,
        "identity_hash": identity_hash,
        "portrait_path": avatar.asset_url,
        "portrait_hash": portrait_hash,
        "identity_reference_role": (
            "portrait" if outfit_identity_matches(outfit.source_json, avatar.seed_fullbody_url) else "fullbody"
        ),
        "outfit": ComboMaterialRef(
            material_id=outfit.id,
            name=outfit.name,
            path=outfit.fullbody_url,
            content_hash=outfit_hash,
            description=outfit.description or "",
            revision=outfit.updated_at,
        ),
        "action": ActionMaterialRef(
            action_id=action.id,
            revision=action.updated_at,
            name=action.name,
            motion_description=action.motion_description,
            clip_kind=action.clip_kind,
            duration_seconds=action.duration_seconds,
            semantic_tags=action.semantic_tags,
            performance_role=action.performance_role,
            motion_intensity=action.motion_intensity,
            interruptible=action.interruptible,
            use_when=action.use_when,
            avoid_when=action.avoid_when,
            allowed_triggers=action.allowed_triggers,
        ),
        "plan": None,
        "requirements": requirements,
        "persona": persona_definition,
    }
    if asset_kind == "actor":
        source: ActorClipSource | CinematicSource = ActorClipSource(kind="actor", **shared)
    else:
        source = CinematicSource(
            kind="cinematic",
            scene=SceneMediaRef(
                scene_id=scene.id,
                title=scene.title,
                path=scene.media_path,
                content_hash=scene_hash or "",
                description=scene.description,
                revision=scene.updated_at,
            ),
            **shared,
        )
    return VisualSnapshot(source=source, target_width=width, target_height=height)


async def design_action_reference(
    user_id: int,
    *,
    name: str,
    motion_description: str,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    allowed_triggers: list[str] | None = None,
    semantic_tags: list[SemanticTag] | None = None,
    performance_role: ActionPerformanceRole | None = None,
    motion_intensity: ActionMotionIntensity = "normal",
    interruptible: bool = True,
    reason: str = "",
    duration_seconds: int = 4,
    clip_kind: ActionClipKind = "once",
    source: ActionAssetSource = "autonomous",
    life_step: LifeStepRequest | None = None,
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
        semantic_tags=semantic_tags or [],
        performance_role=performance_role,
        motion_intensity=motion_intensity,
        interruptible=interruptible,
        reason=reason,
    )
    async with SESSION_LOCAL() as db:
        review_source = await load_action_review_source(db, user_id)
        view = await action_service.create_action(
            db,
            user_id,
            design,
            source=source,
            review_source_json=review_source.model_dump(mode="json"),
            life_step=life_step,
        )
        await db.commit()
    if view.status == "pending":
        schedule_action_review(user_id, view.id, view.updated_at)
    messages = {
        "pending": "已登记，等待评审",
        "reviewing": "相同动作正在评审",
        "approved": "复用已通过的相同动作",
        "deferred": "相同动作待补充资料，请根据评审说明补充后重试",
        "rejected": "相同动作已被拒绝，调整定义后重新提交",
        "failed": "相同动作评审未完成，请在未入库动作中重试评审",
    }
    return {
        "action_id": view.id,
        "name": view.name,
        "status": view.status,
        "message": messages[view.status],
        "review_reason": view.review_reason,
    }


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
        current = await action_queries.action_detail(db, user_id, action_id)
        if current is None or action_service.prepare_action_update(current, body, body.expected_updated_at) is None:
            return current
        source = await load_action_review_source(db, user_id)
        view = await action_service.update_action(
            db,
            user_id,
            action_id,
            body,
            expected_updated_at=body.expected_updated_at,
            review_source_json=source.model_dump(mode="json"),
        )
        await db.commit()
    if view is not None and view.status == "pending":
        schedule_action_review(user_id, action_id, view.updated_at)
    return view


async def retry_action_reference(user_id: int, action_id: int, body: ActionReviewRequest) -> ActionAssetView | None:
    async with SESSION_LOCAL() as db:
        current = await action_queries.action_detail(db, user_id, action_id)
        if current is None:
            return None
        action_service.require_review_retry(current, body.expected_updated_at)
        source = await load_action_review_source(db, user_id)
        view = await action_service.retry_review(
            db,
            user_id,
            action_id,
            expected_updated_at=body.expected_updated_at,
            review_source_json=source.model_dump(mode="json"),
        )
        await db.commit()
    if view is not None:
        schedule_action_review(user_id, action_id, view.updated_at)
    return view


async def generate_visual_reference(
    user_id: int,
    *,
    request_id: str,
    outfit_id: int,
    scene_id: int | None,
    action_id: int,
    asset_kind: VisualAssetKind = "cinematic",
    media_kind: VisualMediaKind = "image",
    requirements: str = "",
    feedback: str = "",
    video_feedback: str = "",
    life_step: LifeStepRequest | None = None,
) -> dict[str, Any]:
    """冻结本次制作输入并提交工作项；空影像库也能开始第一次制作。"""

    async with user_asset_lock(user_id):
        state = await presentation_state(user_id, with_catalog=False)
        if not state.autonomous_enabled:
            raise ComboAssemblyError("伙伴自主制作已关闭")
        async with SESSION_LOCAL() as db:
            combo = await load_visual_snapshot(
                db,
                user_id,
                asset_kind=asset_kind,
                outfit_id=outfit_id,
                scene_id=scene_id,
                action_id=action_id,
                requirements=requirements,
                target_width=state.target_width,
                target_height=state.target_height,
            )
            await db.commit()
        request_key = (
            life_step.idempotency_key
            if life_step is not None
            else "life:visual:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        )
        life_state = await read_state(user_id)
        action_name = combo.source.action.name if combo.source.kind != "scene" else combo.source.scene.title
        request = life_step or LifeStepRequest(
            idempotency_key=request_key,
            kind="visual.generate",
            description=f"制作伙伴影像：{action_name}",
            pursuit_id=life_state.active_pursuit_id,
        )
        job = await visuals_service.create_visual_job(
            user_id,
            snapshot=combo,
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


async def generate_actor_clip_reference(
    user_id: int,
    *,
    request_id: str,
    outfit_id: int,
    action_id: int,
    requirements: str = "",
    feedback: str = "",
    video_feedback: str = "",
    target_width: int | None = None,
    target_height: int | None = None,
) -> VisualJobView:
    """用户制作透明主体动画；请求重放沿原任务，不重新读取可变素材。"""

    async with user_asset_lock(user_id):
        request_key = "manual:visual:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        existing = await creation_request(user_id, request_key)
        if existing is not None:
            job, saved = existing
            source = saved.source
            if (
                source.kind != "actor"
                or source.outfit.material_id != outfit_id
                or source.action.action_id != action_id
                or (source.requirements, source.feedback, source.video_feedback)
                != (requirements, feedback, video_feedback)
            ):
                raise ComboAssemblyError("这个请求已用于另一次制作，请核对原任务")
            return job
        async with SESSION_LOCAL() as db:
            combo = await load_visual_snapshot(
                db,
                user_id,
                asset_kind="actor",
                outfit_id=outfit_id,
                scene_id=None,
                action_id=action_id,
                requirements=requirements,
                target_width=target_width or 1024,
                target_height=target_height or 1024,
            )
            await db.commit()
        return await visuals_service.create_visual_job(
            user_id,
            snapshot=combo,
            target_kind="video",
            source="user_requested",
            feedback=feedback,
            video_feedback=video_feedback,
            request_id=request_key,
        )


async def generate_scene_media_reference(
    user_id: int,
    *,
    request_id: str,
    scene_id: int,
    requirements: str = "",
    feedback: str = "",
    video_feedback: str = "",
    target_width: int | None = None,
    target_height: int | None = None,
) -> VisualJobView:
    """冻结环境媒体的输入并提交：单张环境素材制作环境循环视频。"""

    async with user_asset_lock(user_id):
        request_key = "manual:visual:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        existing = await creation_request(user_id, request_key)
        if existing is not None:
            job, saved = existing
            source = saved.source
            if (
                source.kind != "scene"
                or source.scene.scene_id != scene_id
                or (source.requirements, source.feedback, source.video_feedback)
                != (requirements, feedback, video_feedback)
            ):
                raise ComboAssemblyError("这个请求已用于另一次制作，请核对原任务")
            return job
        state = await presentation_state(user_id, with_catalog=False)
        async with SESSION_LOCAL() as db:
            scene = await scene_snapshot(db, user_id, scene_id)
            if scene is None or scene.status != "ready" or not scene.media_path:
                raise ComboAssemblyError("请选择已就绪的环境图片")
            scene_hash = await asyncio.to_thread(_asset_hash, scene.media_path)
            combo = VisualSnapshot(
                source=SceneMediaSource(
                    scene=SceneMediaRef(
                        scene_id=scene.id,
                        title=scene.title,
                        path=scene.media_path,
                        content_hash=scene_hash,
                        description=scene.description,
                        revision=scene.updated_at,
                    ),
                    requirements=requirements,
                    feedback=feedback,
                    video_feedback=video_feedback,
                ),
                target_width=target_width or state.target_width or DEFAULT_TARGET[0],
                target_height=target_height or state.target_height or DEFAULT_TARGET[1],
            )
            await db.commit()
        return await visuals_service.create_visual_job(
            user_id,
            snapshot=combo,
            target_kind="video",
            source="user_requested",
            feedback=feedback,
            video_feedback=video_feedback,
            request_id=request_key,
        )


async def play_visual_reference(
    user_id: int,
    visual_id: int,
    *,
    proactive_turn: bool = False,
    expected_state_revision: int | None = None,
    expected_life_revision: int | None = None,
) -> dict[str, Any]:
    """请求呈现已有影像；受理不等于已播放，实际回执才更新呈现事实。"""

    if proactive_turn:
        record = get_user_proactive_record(user_id)
        if record.busy_count or await get_disturbance_tier(user_id) != "autonomous":
            return {"status": "blocked", "message": "当前活动或打扰档位不适合自主呈现"}
    try:
        command = await visuals_service.play_visual(
            user_id,
            visual_id,
            source="autonomous",
            expected_state_revision=expected_state_revision,
            expected_life_revision=expected_life_revision,
        )
    except visuals_service.VisualPresentationConflictError:
        return {"status": "state_changed"}
    return {
        "play_id": command.play_id,
        "visual_id": command.visual_id,
        "media_kind": command.media_kind,
        "status": "queued",
        "message": "已请求呈现，实际播放以桌面回执为准",
    }
