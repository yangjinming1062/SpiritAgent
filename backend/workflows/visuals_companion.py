"""伙伴影像与动作素材的跨业务编排：组合冻结、模型工具与评审调度。

组合在本层冻结为不可变快照，各业务模块只经公开查询和 DTO 协作；登记动作素材本身不启动制作。
"""

import asyncio
import hashlib
import re
from typing import Any, Literal

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
from features.appearance.identity import VisualIdentityNotReadyError, require_visual_identity
from features.appearance.queries import (
    avatar_snapshot,
    count_ready_outfits,
    outfit_identity_matches,
    ready_outfit_snapshot,
    ready_outfit_snapshots,
    search_outfit_snapshots,
)
from features.appearance.query_contracts import OutfitSnapshot
from features.companion.definition import visual_persona_definition
from features.companion.disturbance import get_disturbance_tier
from features.companion.persona_service import load_persona_definition
from features.companion.proactive_runtime import get_user_proactive_record
from features.companion.visual_state import persona_snapshot
from features.life.schemas import LifeStepRequest
from features.life.use_cases import read_state
from features.media.visual_semantics import ActionMotionIntensity, ActionPerformanceRole, DayPhase, SemanticTag
from features.scenes.queries import count_ready_scenes, list_scenes, scene_snapshot
from features.scenes.query_contracts import SceneSnapshot
from features.visuals import service as visuals_service
from features.visuals.interpret import interpret_visual_description
from features.visuals.queries import creation_request, presentation_state
from features.visuals.schemas import (
    ActionMaterialRef,
    ActorClipSource,
    CinematicSource,
    ComboMaterialRef,
    SceneMediaRef,
    SceneMediaSource,
    VisualAssetKind,
    VisualIntent,
    VisualInterpretation,
    VisualJobView,
    VisualMediaKind,
    VisualPlanDraft,
    VisualSnapshot,
)
from infrastructure.assets.asset_store import client_asset_url, compute_file_sha256, resolve_asset_reference
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


def freeze_action_reference(action: ActionAssetView) -> ActionMaterialRef:
    return ActionMaterialRef(
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
    )


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
        identity = await require_visual_identity(db, user_id)
    except VisualIdentityNotReadyError as exc:
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
    persona_definition = visual_persona_definition(load_persona_definition(persona)) if persona is not None else {}
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
        "action": freeze_action_reference(action),
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
    request_key: str | None = None,
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
            request_key=request_key,
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
    identity = await require_visual_identity(db, user_id)
    avatar = await avatar_snapshot(db, user_id, identity.avatar_id)
    persona = await persona_snapshot(db, user_id)
    if avatar is None or not avatar.seed_fullbody_url or not avatar.asset_url:
        raise ComboAssemblyError("伙伴的形象尚未准备好")
    identity_hash, portrait_hash = await asyncio.gather(
        asyncio.to_thread(_asset_hash, avatar.seed_fullbody_url), asyncio.to_thread(_asset_hash, avatar.asset_url)
    )
    return ActionReviewSource(
        identity_path=avatar.seed_fullbody_url,
        identity_hash=identity_hash,
        portrait_path=avatar.asset_url,
        portrait_hash=portrait_hash,
        identity=identity,
        persona=visual_persona_definition(load_persona_definition(persona)) if persona is not None else {},
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
    narrative: str | None = None,
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
            narrative=narrative,
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
        return {"job_id": job.id, "state": job.state, "target_kind": job.target_kind, "message": "制作已受理"}


async def generate_character_visual_reference(
    user_id: int,
    *,
    request_id: str,
    outfit_id: int,
    action_id: int,
    asset_kind: Literal["actor", "cinematic"] = "actor",
    scene_id: int | None = None,
    media_kind: VisualMediaKind = "video",
    requirements: str = "",
    feedback: str = "",
    video_feedback: str = "",
    target_width: int | None = None,
    target_height: int | None = None,
) -> VisualJobView:
    """用户组合制作；请求重放沿原任务，不重新读取可变素材，新请求先复核引用素材。"""

    async with user_asset_lock(user_id):
        request_key = "manual:visual:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        existing = await creation_request(user_id, request_key)
        if existing is not None:
            job, saved = existing
            source = saved.source
            if (
                source.kind != asset_kind
                or job.target_kind != media_kind
                or source.outfit.material_id != outfit_id
                or source.action.action_id != action_id
                or (source.kind == "cinematic" and source.scene.scene_id != scene_id)
                or (source.requirements, source.feedback, source.video_feedback)
                != (requirements, feedback, video_feedback)
            ):
                raise visuals_service.VisualRequestConflictError("这个请求已用于另一次制作，请核对原任务")
            return job
        await _require_materials(
            user_id, outfit_id=outfit_id, scene_id=scene_id if asset_kind == "cinematic" else None, action_id=action_id
        )
        async with SESSION_LOCAL() as db:
            combo = await load_visual_snapshot(
                db,
                user_id,
                asset_kind=asset_kind,
                outfit_id=outfit_id,
                scene_id=scene_id if asset_kind == "cinematic" else None,
                action_id=action_id,
                requirements=requirements,
                target_width=target_width or (1024 if asset_kind == "actor" else DEFAULT_TARGET[0]),
                target_height=target_height or (1024 if asset_kind == "actor" else DEFAULT_TARGET[1]),
            )
            await db.commit()
        return await visuals_service.create_visual_job(
            user_id,
            snapshot=combo,
            target_kind=media_kind,
            source="user_requested",
            feedback=feedback,
            video_feedback=video_feedback,
            request_id=request_key,
        )


_CANDIDATE_LIMIT = 40
_MAX_KEYWORDS = 12
# 中英文停用词不参与候选检索；材料名称与场景词保留。
_STOP_WORDS = frozenset(
    [
        "的",
        "了",
        "在",
        "是",
        "和",
        "与",
        "有",
        "我",
        "你",
        "他",
        "她",
        "它",
        "我们",
        "你们",
        "他们",
        "她们",
        "它们",
        "一个",
        "一些",
        "一起",
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "by",
        "with",
        "for",
        "at",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "me",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "my",
        "your",
        "his",
        "her",
        "its",
        "our",
        "their",
        "this",
        "that",
        "these",
        "those",
    ]
)
# CJK 单字与英文/数字词都可作为关键词。
_WORD_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z][A-Za-z0-9_-]*|\d+")


def _description_keywords(description: str) -> list[str]:
    """提取候选检索关键词：CJK 单字与英文/数字词，去停用词、去重并保持出现顺序。"""

    seen: set[str] = set()
    words: list[str] = []
    for word in _WORD_PATTERN.findall(description):
        if word.lower() not in _STOP_WORDS and word not in seen:
            seen.add(word)
            words.append(word)
    return words[:_MAX_KEYWORDS]


def _outfit_view(item: OutfitSnapshot) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "name": item.name,
        "description": (item.description or "")[:80],
        "preview_url": client_asset_url(item.fullbody_url) if item.fullbody_url else None,
    }


def _scene_view(item: SceneSnapshot) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "name": item.title,
        "description": (item.description or "")[:80],
        "preview_url": client_asset_url(item.media_path) if item.media_path else None,
    }


def _action_view(item: ActionAssetView) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "name": item.name,
        "motion_description": item.motion_description[:80],
        "clip_kind": item.clip_kind,
    }


async def _load_visual_candidates(
    db: AsyncSession, user_id: int, description: str
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    """按描述关键词先筛候选再交模型；每类至多 _CANDIDATE_LIMIT 条。

    检索为空但该类素材总数非零时补读最新一页，避免把未命中误判成素材不存在；
    此时目录标记为不完整，由模型在 reason 里说明未列全。
    """

    keywords = _description_keywords(description)
    outfits = await search_outfit_snapshots(db, user_id, keywords, limit=_CANDIDATE_LIMIT)
    scenes, _total, _cursor = await list_scenes(db, user_id, keywords=keywords, ready_only=True, limit=_CANDIDATE_LIMIT)
    actions = await action_queries.list_actions(
        db, user_id, group="in_library", keywords=keywords, limit=_CANDIDATE_LIMIT
    )

    truncated = False
    if not outfits and await count_ready_outfits(db, user_id):
        outfits = await ready_outfit_snapshots(db, user_id, limit=_CANDIDATE_LIMIT)
        truncated = True
    if not scenes and await count_ready_scenes(db, user_id):
        scenes, _t, _c = await list_scenes(db, user_id, ready_only=True, limit=_CANDIDATE_LIMIT)
        truncated = True
    if not actions.actions and await action_queries.count_library_actions(db, user_id):
        actions = await action_queries.list_actions(db, user_id, group="in_library", limit=_CANDIDATE_LIMIT)
        truncated = True

    catalog: dict[str, list[dict[str, Any]]] = {
        "outfits": [_outfit_view(item) for item in outfits],
        "scenes": [_scene_view(item) for item in scenes],
        "actions": [_action_view(item) for item in actions.actions],
    }
    return catalog, truncated


async def interpret_visual_reference(
    user_id: int,
    description: str,
    intent: VisualIntent,
    scene_id: int | None = None,
    day_phase: DayPhase | None = None,
) -> VisualInterpretation:
    """按描述筛选候选素材后整理；整理本身不写库、不受理制作。"""

    async with SESSION_LOCAL() as db:
        catalog, truncated = await _load_visual_candidates(db, user_id, description)
        if scene_id is not None:
            # 环境详情发起的动态制作：直接读取指定环境，不受候选目录影响。
            fixed_scene = await scene_snapshot(db, user_id, scene_id)
            if fixed_scene is None or fixed_scene.status != "ready" or not fixed_scene.media_path:
                raise ValueError("所选环境不存在或尚未就绪")
            catalog["scenes"] = [_scene_view(fixed_scene)]
    return await interpret_visual_description(
        user_id,
        description,
        intent,
        catalog,
        catalog_truncated=truncated,
        scene_id=scene_id,
        day_phase=day_phase,
    )


class VisualDraftError(ValueError):
    """整理出的草稿在创建前复核不通过；code 供客户端判断草稿是否需要重新整理。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require_ready_scene(scene: SceneSnapshot | None) -> None:
    """环境素材必须存在且已就绪；整理出的草稿按同一错误码判断是否需要重新整理。"""

    if scene is None:
        raise VisualDraftError("material_missing", "所选环境不存在")
    if scene.status != "ready" or not scene.media_path:
        raise VisualDraftError("material_not_ready", "所选环境尚未就绪")


async def _require_materials(
    user_id: int, *, outfit_id: int | None = None, scene_id: int | None = None, action_id: int | None = None
) -> None:
    """按引用素材一次性复核归属与就绪状态；仅未受理的新请求需要。"""

    async with SESSION_LOCAL() as db:
        outfit = await ready_outfit_snapshot(db, user_id, outfit_id) if outfit_id is not None else None
        scene = await scene_snapshot(db, user_id, scene_id) if scene_id is not None else None
        action = await action_queries.action_detail(db, user_id, action_id) if action_id is not None else None

    if outfit_id is not None and (outfit is None or not outfit.fullbody_url):
        raise VisualDraftError("material_not_ready", "所选造型不存在或尚未就绪")
    if scene_id is not None:
        _require_ready_scene(scene)
    if action_id is not None:
        if action is None:
            raise VisualDraftError("material_missing", "所选动作不存在")
        if action.status != "approved":
            raise VisualDraftError("material_not_ready", "所选动作尚未通过评审")


async def create_visual_from_draft(user_id: int, request_id: str, draft: VisualPlanDraft) -> VisualJobView:
    """统一创建入口：按资产类型分派到既有制作用例，重放请求先找回原任务。"""

    if draft.asset_kind == "actor":
        if draft.outfit_id is None or draft.action_id is None:
            raise VisualDraftError("material_mismatch", "透明主体动画需要造型与动作")
        if draft.scene_id is not None or draft.media_kind != "video":
            raise VisualDraftError("material_mismatch", "透明主体动画只能是视频，且不绑定环境")
        return await generate_character_visual_reference(
            user_id,
            request_id=f"manual:{request_id}",
            outfit_id=draft.outfit_id,
            action_id=draft.action_id,
            requirements=draft.requirements,
        )

    if draft.asset_kind == "scene":
        if draft.scene_id is None or draft.media_kind != "video":
            raise VisualDraftError("material_mismatch", "环境动态只能是视频，且只需要环境")
        if draft.outfit_id is not None or draft.action_id is not None:
            raise VisualDraftError("material_mismatch", "环境动态只使用环境，不带造型与动作")
        return await generate_scene_media_reference(
            user_id,
            request_id=f"manual:{request_id}",
            scene_id=draft.scene_id,
            day_phase=draft.day_phase,
            requirements=draft.requirements,
        )

    if draft.outfit_id is None or draft.scene_id is None or draft.action_id is None:
        raise VisualDraftError("material_mismatch", "完整场景影像需要造型、环境与动作")
    return await generate_character_visual_reference(
        user_id,
        request_id=f"manual:{request_id}",
        asset_kind="cinematic",
        outfit_id=draft.outfit_id,
        scene_id=draft.scene_id,
        action_id=draft.action_id,
        media_kind=draft.media_kind,
        requirements=draft.requirements,
    )


async def generate_scene_media_reference(
    user_id: int,
    *,
    request_id: str,
    scene_id: int,
    day_phase: DayPhase | None = None,
    requirements: str = "",
    feedback: str = "",
    video_feedback: str = "",
    target_width: int | None = None,
    target_height: int | None = None,
) -> VisualJobView:
    """冻结环境媒体的输入并提交：单张环境素材制作环境循环视频；重放沿原任务。"""

    async with user_asset_lock(user_id):
        request_key = "manual:visual:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        existing = await creation_request(user_id, request_key)
        if existing is not None:
            job, saved = existing
            source = saved.source
            if (
                source.kind != "scene"
                or source.scene.scene_id != scene_id
                or source.day_phase != day_phase
                or (source.requirements, source.feedback, source.video_feedback)
                != (requirements, feedback, video_feedback)
            ):
                raise visuals_service.VisualRequestConflictError("这个请求已用于另一次制作，请核对原任务")
            return job
        async with SESSION_LOCAL() as db:
            scene = await scene_snapshot(db, user_id, scene_id)
        _require_ready_scene(scene)
        state = await presentation_state(user_id, with_catalog=False)
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
                day_phase=day_phase,
                requirements=requirements,
                feedback=feedback,
                video_feedback=video_feedback,
            ),
            target_width=target_width or state.target_width or DEFAULT_TARGET[0],
            target_height=target_height or state.target_height or DEFAULT_TARGET[1],
        )
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
