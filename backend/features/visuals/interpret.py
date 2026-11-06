"""把用户想看的一个画面整理成制作计划；只产出草稿，不扣额度、不受理制作。"""

from typing import Any, Literal

from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import resolve_language
from pydantic import BaseModel, ConfigDict, Field

from features.configuration.settings.values import get_user_setting
from features.media.described import DescribedDetail
from features.media.visual_semantics import DayPhase

from .prompts import VISUAL_COMPOSE_REPAIR, VISUAL_COMPOSE_TEXTS
from .schemas import (
    VisualIntent,
    VisualInterpretation,
    VisualMaterialView,
    VisualNextStep,
    VisualPlanDraft,
)

_DAY_PHASES = ("dawn", "day", "dusk", "night")

_LABELS: dict[str, dict[str, str]] = {
    "zh": {
        "outfit": "造型",
        "scene": "环境",
        "action": "动作",
        "output": "产物",
        "image": "图片",
        "video": "视频",
        "phase": "时段",
        "phases": {"dawn": "黎明", "day": "白天", "dusk": "黄昏", "night": "夜间"},
        "no_action": "还没有能做这个动作的素材，先创建一个动作再试。",
        "no_outfit": "还没有可用的造型，先准备一套造型再试。",
        "no_scene": "还没有合适的环境，先创建一个环境再试。",
        "mismatch": "没有找到与描述匹配的素材，换一种说法或先补充素材。",
        "unavailable": "整理描述所需的模型服务尚未配置，请联系管理员。",
    },
    "en": {
        "outfit": "Outfit",
        "scene": "Environment",
        "action": "Action",
        "output": "Result",
        "image": "Image",
        "video": "Video",
        "phase": "Light",
        "phases": {"dawn": "Dawn", "day": "Day", "dusk": "Dusk", "night": "Night"},
        "no_action": "No action can express this yet. Create one first.",
        "no_outfit": "No ready outfit yet. Prepare one first.",
        "no_scene": "No fitting environment yet. Create one first.",
        "mismatch": "Nothing in the library matches that description yet.",
        "unavailable": "The model service needed to interpret descriptions is not configured.",
    },
}


class _ComposeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    feasible: bool
    reason: str = Field(default="", max_length=300)
    outfit_id: str | None = None
    scene_id: str | None = None
    action_id: str | None = None
    day_phase: str | None = None
    requirements: str = Field(default="", max_length=2000)
    next_step: Literal["create_action", "create_outfit", "create_scene"] | None = None


def _infeasible(reason: str, next_step: VisualNextStep | None) -> VisualInterpretation:
    return VisualInterpretation(feasible=False, reason=reason, next_step=next_step)


def _details(
    language: str,
    draft: VisualPlanDraft,
    outfit_name: str | None,
    scene_name: str | None,
    action_name: str | None,
) -> list[DescribedDetail]:
    labels = _LABELS.get(language, _LABELS["zh"])
    items: list[DescribedDetail] = []
    if outfit_name:
        items.append(DescribedDetail(label=labels["outfit"], value=outfit_name))
    if scene_name:
        items.append(DescribedDetail(label=labels["scene"], value=scene_name))
    if action_name:
        items.append(DescribedDetail(label=labels["action"], value=action_name))
    items.append(
        DescribedDetail(
            label=labels["output"],
            value=labels["image"] if draft.media_kind == "image" else labels["video"],
        )
    )
    if draft.day_phase:
        items.append(DescribedDetail(label=labels["phase"], value=labels["phases"][draft.day_phase]))
    return items


async def interpret_visual_description(
    user_id: int,
    description: str,
    intent: VisualIntent,
    catalog: dict[str, list[dict[str, Any]]],
    *,
    catalog_truncated: bool = False,
    scene_id: int | None = None,
    day_phase: DayPhase | None = None,
) -> VisualInterpretation:
    """整理描述并只在给定目录内选素材；目录由调用方按账户读取。"""

    async with SESSION_LOCAL() as db:
        language = resolve_language(await get_user_setting(db, user_id, "language"))

    labels = _LABELS.get(language, _LABELS["zh"])
    outfits_by_id = {item["id"]: item for item in catalog.get("outfits", [])}
    scenes_by_id = {item["id"]: item for item in catalog.get("scenes", [])}
    actions_by_id = {item["id"]: item for item in catalog.get("actions", [])}

    fixed_scene = scenes_by_id.get(str(scene_id)) if scene_id is not None else None
    if intent == "scenery" and fixed_scene is None:
        return _infeasible(labels["no_scene"], "create_scene")

    config = await load_user_llm_config(user_id, AiTaskKey.visuals)
    if not config.is_configured:
        raise ValueError(labels["unavailable"])

    payload = {
        "output_language": language,
        "intent": intent,
        "user_description": description,
        "fixed_scene": fixed_scene,
        "requested_day_phase": day_phase,
        "catalog": catalog,
        "catalog_truncated": catalog_truncated,
    }

    output = await call_llm_json(
        config,
        VISUAL_COMPOSE_TEXTS.get(language, VISUAL_COMPOSE_TEXTS["zh"]),
        payload,
        schema=_ComposeOutput.model_json_schema(),
        schema_name="visual_compose",
        parse_output=_ComposeOutput.model_validate,
        repair_prompt=VISUAL_COMPOSE_REPAIR,
    )

    if not output.feasible:
        return _infeasible(output.reason or labels["mismatch"], output.next_step)

    outfit = outfits_by_id.get(output.outfit_id or "")
    scene = fixed_scene or scenes_by_id.get(output.scene_id or "")
    action = actions_by_id.get(output.action_id or "")
    phase = output.day_phase if output.day_phase in _DAY_PHASES else None

    if intent == "scenery":
        if scene is None:
            return _infeasible(labels["no_scene"], "create_scene")
        draft = VisualPlanDraft(
            asset_kind="scene",
            media_kind="video",
            scene_id=int(scene["id"]),
            day_phase=day_phase or phase,
            requirements=output.requirements,
        )
        return VisualInterpretation(
            feasible=True,
            draft=draft.model_dump(mode="json"),
            summary=scene["name"],
            details=_details(language, draft, None, scene["name"], None),
            materials=[
                VisualMaterialView(
                    role="scene", id=int(scene["id"]), name=scene["name"], preview_url=scene["preview_url"]
                )
            ],
        )

    if outfit is None:
        return _infeasible(labels["no_outfit"], "create_outfit")
    if action is None:
        return _infeasible(labels["no_action"], "create_action")

    if intent == "motion":
        draft = VisualPlanDraft(
            asset_kind="actor",
            media_kind="video",
            outfit_id=int(outfit["id"]),
            action_id=int(action["id"]),
            requirements=output.requirements,
        )
        return VisualInterpretation(
            feasible=True,
            draft=draft.model_dump(mode="json"),
            summary=f"{action['name']} · {outfit['name']}",
            details=_details(language, draft, outfit["name"], None, action["name"]),
            materials=[
                VisualMaterialView(
                    role="outfit", id=int(outfit["id"]), name=outfit["name"], preview_url=outfit["preview_url"]
                ),
                VisualMaterialView(role="action", id=int(action["id"]), name=action["name"]),
            ],
        )

    if scene is None:
        return _infeasible(labels["no_scene"], "create_scene")

    draft = VisualPlanDraft(
        asset_kind="cinematic",
        media_kind="image" if intent == "photo" else "video",
        outfit_id=int(outfit["id"]),
        scene_id=int(scene["id"]),
        action_id=int(action["id"]),
        requirements=output.requirements,
    )
    return VisualInterpretation(
        feasible=True,
        draft=draft.model_dump(mode="json"),
        summary=f"{action['name']} · {scene['name']}",
        details=_details(language, draft, outfit["name"], scene["name"], action["name"]),
        materials=[
            VisualMaterialView(
                role="outfit", id=int(outfit["id"]), name=outfit["name"], preview_url=outfit["preview_url"]
            ),
            VisualMaterialView(role="scene", id=int(scene["id"]), name=scene["name"], preview_url=scene["preview_url"]),
            VisualMaterialView(role="action", id=int(action["id"]), name=action["name"]),
        ],
    )
