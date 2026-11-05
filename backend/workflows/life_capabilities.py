"""生活能力政策与素材快照；日常判断、夜间规划和执行前复核共用。"""

from dataclasses import dataclass
from typing import Any, Literal

from features.accounts.queries import nightly_account, user_is_active
from features.action_library.queries import list_actions
from features.action_library.schemas import ActionAssetView
from features.appearance.identity import IDENTITY_NOT_CONFIRMED, load_visual_identity
from features.appearance.queries import ready_outfit_snapshots
from features.appearance.query_contracts import OutfitSnapshot
from features.companion.persona_service import onboarded_user_ids
from features.companion.visual_state import persona_snapshot
from features.configuration.settings.values import load_user_settings
from features.life.actions import LIFE_CAPABILITIES, ReadingAction, VisualAction
from features.life.schemas import LifeStepView
from features.media.image_generation import resolve_image_gen_chain
from features.scenes.query_contracts import SceneSnapshot
from features.scenes.snapshots import list_scene_snapshots
from features.topics.store import load_prompt_topics
from features.visuals.queries import presentation_state
from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.models import ModelModality
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import utc_now
from infrastructure.web.extract_provider import resolve_extract_provider
from pydantic import BaseModel
from runtime.memory_scope import MemoryScope
from runtime.tools.toolsets import disabled_backend_tool_names


@dataclass(frozen=True)
class CapabilityBlock:
    disposition: Literal["permission_required", "unavailable"]
    reason: str


@dataclass(frozen=True)
class LifeMaterials:
    """当前可自动使用的素材；顺序由各领域查询决定，首个即优先项。"""

    outfits: tuple[OutfitSnapshot, ...]
    scenes: tuple[SceneSnapshot, ...]
    actions: tuple[ActionAssetView, ...]

    @property
    def visual(self) -> tuple[OutfitSnapshot, SceneSnapshot, ActionAssetView] | None:
        if self.outfits and self.scenes and self.actions:
            return self.outfits[0], self.scenes[0], self.actions[0]
        return None


async def load_life_materials(user_id: int) -> LifeMaterials:
    async with SESSION_LOCAL() as db:
        outfits = await ready_outfit_snapshots(db, user_id)
        scenes = (await list_scene_snapshots(db, user_id, ready_only=True, limit=50)).scenes
        actions = (await list_actions(db, user_id, status="approved", limit=50)).actions
    return LifeMaterials(tuple(outfits), tuple(scenes), tuple(actions))


def capability_schema(capability: str, *, model: type[BaseModel] | None = None) -> dict[str, Any]:
    """返回能力的 JSON Schema，去掉由调用方决定的判别字段。"""
    schema = (model or LIFE_CAPABILITIES[capability].input_model).model_json_schema()
    schema["properties"].pop("capability", None)
    schema["required"] = [key for key in schema.get("required", []) if key != "capability"]
    return schema


async def capability_blocks(
    user_id: int,
    *,
    source: str = "life",
    capabilities: tuple[str, ...] | None = None,
    materials: LifeMaterials | None = None,
) -> dict[str, CapabilityBlock]:
    """规划目录与执行前复核共用政策、身份、开关和模型资格。"""
    targets = set(LIFE_CAPABILITIES if capabilities is None else capabilities)
    async with SESSION_LOCAL() as db:
        if not await user_is_active(db, user_id) or user_id not in await onboarded_user_ids(db, (user_id,)):
            return dict.fromkeys(targets, CapabilityBlock("unavailable", "账户未激活或伙伴引导尚未完成"))
        identity_ready = await load_visual_identity(db, user_id) is not None
        persona = await persona_snapshot(db, user_id)
        disabled = disabled_backend_tool_names(await load_user_settings(db, user_id))
        if source == "nightly":
            account = await nightly_account(db, user_id)
            if account is None or not account.nightly_activity_enabled:
                return dict.fromkeys(targets, CapabilityBlock("permission_required", "夜间自主活动已关闭"))
    if persona is None:
        return dict.fromkeys(targets, CapabilityBlock("unavailable", "伙伴资料尚未就绪"))
    planning_task = AiTaskKey.nightly if source == "nightly" else AiTaskKey.proactive
    if not (await load_user_llm_config(user_id, planning_task)).is_configured:
        return dict.fromkeys(targets, CapabilityBlock("unavailable", "生活判断模型尚未配置"))
    blocked: dict[str, CapabilityBlock] = {}
    if not identity_ready:
        blocked.update(
            dict.fromkeys(
                ("outfit.create", "visual.generate", "action.design"),
                CapabilityBlock("unavailable", IDENTITY_NOT_CONFIRMED),
            )
        )
    if persona.outfit_policy == "locked":
        blocked["outfit.create"] = CapabilityBlock("permission_required", "自主添置造型已关闭")
    if persona.scene_policy == "locked" or "scene_create" in disabled:
        blocked["scene.create"] = CapabilityBlock("permission_required", "自主添置环境已关闭")
    if not SETTINGS.action_autocreate_enabled:
        blocked["action.design"] = CapabilityBlock("permission_required", "自主设计动作已关闭")
    if "visual.generate" in targets and not (await presentation_state(user_id, with_catalog=False)).autonomous_enabled:
        blocked["visual.generate"] = CapabilityBlock("permission_required", "自主影像制作已关闭")
    if source != "nightly":
        blocked["outreach.schedule"] = CapabilityBlock("permission_required", "后续联系由夜间规划安排")
    async with SESSION_LOCAL() as db:
        for capability, task in (
            ("outfit.create", AiTaskKey.outfit),
            ("scene.create", AiTaskKey.scene),
            ("visual.generate", AiTaskKey.visuals),
        ):
            if capability in targets and capability not in blocked:
                chain = await resolve_image_gen_chain(
                    db, user_id, task=task, has_reference=capability != "scene.create"
                )
                if not chain.providers:
                    blocked[capability] = CapabilityBlock("unavailable", "图片制作模型尚未配置")
    if "visual.generate" in targets and "visual.generate" not in blocked:
        snapshot = materials if materials is not None else await load_life_materials(user_id)
        if snapshot.visual is None:
            blocked["visual.generate"] = CapabilityBlock("unavailable", "缺少可用的造型、环境或动作")
    if (
        "action.design" in targets
        and "action.design" not in blocked
        and not (
            await load_user_llm_config(
                user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
            )
        ).is_configured
    ):
        blocked["action.design"] = CapabilityBlock("unavailable", "动作评审模型尚未配置")
    return {name: problem for name, problem in blocked.items() if name in targets}


async def life_capability_context(user_id: int) -> dict[str, Any]:
    """日常生活判断的行动目录与素材；模型只从此处了解可执行范围。"""
    materials = await load_life_materials(user_id)
    blocked = await capability_blocks(user_id, materials=materials)
    async with SESSION_LOCAL() as db:
        topics = await load_prompt_topics(db, MemoryScope(user_id, "companion"), utc_now())
    return {
        "capabilities": {name: name not in blocked for name in LIFE_CAPABILITIES},
        "blocked_capabilities": {name: problem.reason for name, problem in blocked.items()},
        "capability_descriptions": {
            name: definition.description for name, definition in LIFE_CAPABILITIES.items() if name not in blocked
        },
        "outfits": [
            {"id": str(item.id), "name": item.name, "description": item.description} for item in materials.outfits
        ],
        "scenes": [
            {"id": str(item.id), "title": item.title, "description": item.description} for item in materials.scenes
        ],
        "actions": [
            {"id": str(item.id), "name": item.name, "description": item.motion_description}
            for item in materials.actions
        ],
        "topics": [
            {"id": str(item.topic_id), "title": item.title, "summary": item.summary, "url": item.url} for item in topics
        ],
    }


async def action_block_reason(user_id: int, step: LifeStepView) -> tuple[str, str] | None:
    """已登记步骤的执行前复核；素材与工具条件变化时阻止本次提交。"""
    if problem := (await capability_blocks(user_id, source=step.source, capabilities=(step.kind,))).get(step.kind):
        return problem.disposition, problem.reason
    if isinstance(step.action, ReadingAction):
        async with SESSION_LOCAL() as db:
            disabled = disabled_backend_tool_names(await load_user_settings(db, user_id))
        if (step.action.query and "web_search" in disabled) or (
            (step.action.query or step.action.urls) and "web_extract" in disabled
        ):
            return "permission_required", "网页读取工具已关闭"
        if step.action.query or step.action.urls:
            provider = resolve_extract_provider()
            if not provider.is_available() or not provider.supports_extract():
                return "unavailable", "网页正文提取尚未配置"
    if isinstance(step.action, VisualAction) and step.action.media_kind == "video":
        async with SESSION_LOCAL() as db:
            if not await resolve_provider_chain(db, user_id, "video_gen", task=AiTaskKey.visuals):
                return "unavailable", "视频制作模型尚未配置"
    return None
