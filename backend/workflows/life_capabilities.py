"""生活能力政策与素材快照；日常判断、夜间规划和执行前复核共用。"""

from dataclasses import dataclass
from itertools import zip_longest
from typing import Any, Literal

from features.accounts.queries import nightly_account, user_is_active
from features.action_library.queries import action_planning_materials, count_library_actions
from features.appearance.identity import IDENTITY_NOT_CONFIRMED, load_visual_identity
from features.appearance.queries import count_ready_outfits, outfit_planning_materials
from features.companion.persona_service import onboarded_user_ids
from features.companion.visual_state import persona_snapshot
from features.configuration.settings.values import load_user_settings
from features.life.actions import LIFE_CAPABILITIES, ReadingAction, VisualAction
from features.life.execution import checkpoint
from features.life.schemas import LifeStepView
from features.media.image_generation import resolve_image_gen_chain
from features.scenes.queries import count_ready_scenes, scene_planning_materials
from features.topics.store import ranked_topic_summaries
from features.visuals.queries import creation_preferences
from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.models import ModelModality
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.token_budget import json_tokens
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.search import extract_search_terms
from infrastructure.text import utc_now
from infrastructure.web.extract_provider import resolve_extract_provider, resolve_search_provider
from pydantic import BaseModel
from runtime.memory_scope import MemoryScope
from runtime.tools.toolsets import disabled_backend_tool_names


@dataclass(frozen=True)
class CapabilityBlock:
    disposition: Literal["permission_required", "unavailable"]
    reason: str


type LifeMaterials = dict[str, list[dict[str, str]]]


def fit_life_materials(materials: LifeMaterials, query: str, token_budget: int) -> LifeMaterials:
    """优先取相关素材，三类交替取材，使用本次模型输入的实际余量。"""
    terms = extract_search_terms(query)

    def score(row: dict[str, str]) -> int:
        description = " ".join(row.values()).lower()
        return sum(term in description for term in terms)

    selected: LifeMaterials = {group: [] for group in materials}
    ranked = [sorted(rows, key=score, reverse=True) for rows in materials.values()]
    available = token_budget - json_tokens(selected)
    for candidates in zip_longest(*ranked):
        for group, row in zip(materials, candidates, strict=True):
            if row is not None and (cost := json_tokens(row) + 2) <= available:
                selected[group].append(row)
                available -= cost
    return selected


def validate_visual_materials(action: VisualAction, catalog: dict[str, Any]) -> None:
    for group, identifier in (
        ("outfits", action.outfit_id),
        ("scenes", action.scene_id),
        ("actions", action.action_id),
    ):
        if not any(item["id"] == str(identifier) for item in catalog[group]):
            raise ValueError(f"Select a ready material from {group}")


async def load_life_materials(user_id: int) -> LifeMaterials:
    async with SESSION_LOCAL() as db:
        return {
            "outfits": await outfit_planning_materials(db, user_id),
            "scenes": await scene_planning_materials(db, user_id),
            "actions": await action_planning_materials(db, user_id),
        }


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
    if source != "nightly":
        blocked["outreach.schedule"] = CapabilityBlock(
            "permission_required", "日常联系用经历的 chat 摘选，次日定时安排由夜间规划登记"
        )
    async with SESSION_LOCAL() as db:
        if "visual.generate" in targets and not (await creation_preferences(db, user_id)).autonomous_enabled:
            blocked["visual.generate"] = CapabilityBlock("permission_required", "自主影像制作已关闭")
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
        if materials is None:
            async with SESSION_LOCAL() as db:
                ready = (
                    await count_ready_outfits(db, user_id)
                    and await count_ready_scenes(db, user_id)
                    and await count_library_actions(db, user_id)
                )
        else:
            ready = all(materials.values())
        if not ready:
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


def _web_reading_block(disabled: set[str], *, search: bool) -> CapabilityBlock | None:
    if "web_extract" in disabled or (search and "web_search" in disabled):
        return CapabilityBlock("permission_required", "网页读取工具已关闭")
    extractor = resolve_extract_provider()
    if not extractor.is_available() or not extractor.supports_extract():
        return CapabilityBlock("unavailable", "网页正文提取尚未配置")
    if search:
        searcher = resolve_search_provider()
        if not searcher.is_available() or not searcher.supports_search():
            return CapabilityBlock("unavailable", "网页搜索尚不可用")
    return None


async def require_web_reading(user_id: int, *, search: bool) -> None:
    async with SESSION_LOCAL() as db:
        disabled = disabled_backend_tool_names(await load_user_settings(db, user_id))
    if problem := _web_reading_block(disabled, search=search):
        raise ValueError(problem.reason)


async def life_capability_context(user_id: int, *, query: str, token_budget: int) -> dict[str, Any]:
    """日常生活判断的行动目录与素材；模型只从此处了解可执行范围。"""
    materials = await load_life_materials(user_id)
    blocked = await capability_blocks(user_id, materials=materials)
    async with SESSION_LOCAL() as db:
        disabled = disabled_backend_tool_names(await load_user_settings(db, user_id))
    url_block = _web_reading_block(disabled, search=False)
    search_block = url_block or _web_reading_block(disabled, search=True)
    execution = {
        "capabilities": {name: name not in blocked for name in LIFE_CAPABILITIES},
        "blocked_capabilities": {name: problem.reason for name, problem in blocked.items()},
        "capability_descriptions": {
            name: definition.description for name, definition in LIFE_CAPABILITIES.items() if name not in blocked
        },
        "reading_sources": {"public_urls": url_block is None, "web_search": search_block is None},
    }
    catalog = fit_life_materials(materials, query, token_budget - json_tokens(execution))
    execution.update(catalog)
    if not all(catalog.values()) and execution["capabilities"]["visual.generate"]:
        execution["capabilities"]["visual.generate"] = False
        execution["blocked_capabilities"]["visual.generate"] = "模型窗口无法容纳一组制作素材"
        execution["capability_descriptions"].pop("visual.generate")
    async with SESSION_LOCAL() as db:
        topics = await ranked_topic_summaries(
            db,
            MemoryScope(user_id, "companion"),
            utc_now(),
            query=query,
            token_budget=token_budget - json_tokens(execution),
        )
    if url_block and not topics:
        execution["capabilities"]["content.read"] = False
        execution["blocked_capabilities"]["content.read"] = url_block.reason
        execution["capability_descriptions"].pop("content.read", None)
    execution["topics"] = topics
    return execution


async def action_block_reason(user_id: int, step: LifeStepView) -> tuple[str, str] | None:
    """已登记步骤的执行前复核；素材与工具条件变化时阻止本次提交。"""
    if problem := (await capability_blocks(user_id, source=step.source, capabilities=(step.kind,))).get(step.kind):
        return problem.disposition, problem.reason
    if isinstance(step.action, ReadingAction) and (step.action.urls or step.action.query):
        async with SESSION_LOCAL() as db:
            disabled = disabled_backend_tool_names(await load_user_settings(db, user_id))
        problem = _web_reading_block(disabled, search=not step.action.urls)
        if problem and not (await checkpoint(user_id, step.id)).get("sources"):
            return problem.disposition, problem.reason
    if isinstance(step.action, VisualAction) and step.action.media_kind == "video":
        async with SESSION_LOCAL() as db:
            if not await resolve_provider_chain(db, user_id, "video_gen", task=AiTaskKey.visuals):
                return "unavailable", "视频制作模型尚未配置"
    return None
