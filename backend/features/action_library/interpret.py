"""把用户的一句日常描述整理成动作定义草稿；只产出草稿，不登记、不评审。"""

from typing import Any, Literal

from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, resolve_language
from pydantic import BaseModel, ConfigDict, Field

from features.configuration.settings.values import get_user_setting
from features.media.described import DescribedDetail
from features.media.visual_semantics import ACTION_SEMANTIC_TAGS

from .prompts import ACTION_COMPOSE_REPAIR, ACTION_COMPOSE_TEXTS
from .queries import action_detail
from .schemas import ActionAssetDesign, ActionAssetView, ActionInterpretation
from .service import require_action_editable

_MOTION_INTENSITIES = frozenset({"subtle", "normal", "expressive"})
_ROLES = frozenset({"idle", "sustained", "reaction"})

_LABELS: dict[str, dict[str, Any]] = {
    "zh": {
        "name": "名称",
        "motion": "动作",
        "use_when": "适用时机",
        "avoid_when": "避免",
        "form": "形式",
        "loop": "持续循环",
        "once": "一次性",
        "seconds": "秒",
        "intensity": "幅度",
        "intensities": {"subtle": "轻微", "normal": "日常", "expressive": "明显"},
        "interruptible": "可被打断",
        "yes": "是",
        "no": "否",
        "unavailable": "整理描述所需的模型服务尚未配置，请联系管理员。",
        "not_found": "未找到该动作素材",
    },
    "en": {
        "name": "Name",
        "motion": "Action",
        "use_when": "When to use",
        "avoid_when": "Avoid",
        "form": "Form",
        "loop": "Loop",
        "once": "One-off",
        "seconds": "s",
        "intensity": "Intensity",
        "intensities": {"subtle": "Subtle", "normal": "Normal", "expressive": "Expressive"},
        "interruptible": "Interruptible",
        "yes": "Yes",
        "no": "No",
        "unavailable": "The model service needed to interpret descriptions is not configured.",
        "not_found": "Action material not found.",
    },
}


class ActionNotFoundError(ValueError):
    """草稿指向的动作不存在或不属于当前账户。"""


class _ComposeOutput(BaseModel):
    """模型输出契约；长度与枚举在解析阶段即被校验，失败走一次格式修正。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64)
    motion_description: str = Field(min_length=10, max_length=600)
    use_when: list[str] = Field(default_factory=list, max_length=8)
    avoid_when: list[str] = Field(default_factory=list, max_length=8)
    clip_kind: Literal["once", "loop"] = "once"
    duration_seconds: int = Field(default=4, ge=1, le=15)
    semantic_tags: list[str] = Field(default_factory=list)
    allowed_triggers: list[Literal["chat", "manual", "periodic", "user_return"]] = Field(
        default_factory=list, max_length=4
    )
    performance_role: str | None = None
    motion_intensity: str = "normal"
    interruptible: bool = True
    reason: str = Field(default="", max_length=400)


def _derived_role(clip_kind: str, role: str | None) -> str:
    """复用后端既有的播放方式与表现用途约束，避免让模型输出非法组合。"""
    if role not in _ROLES:
        return "sustained" if clip_kind == "loop" else "reaction"
    if clip_kind == "loop" and role == "reaction":
        return "sustained"
    if clip_kind == "once" and role in {"idle", "sustained"}:
        return "reaction"
    return role


def _design_from_output(output: _ComposeOutput, preserve_triggers: list[str]) -> ActionAssetDesign:
    """整理结果转定义；触发条件只在模型明确给出时覆盖，否则沿用编辑前的值。"""

    tags = list(dict.fromkeys(tag for tag in output.semantic_tags if tag in ACTION_SEMANTIC_TAGS))[:8]
    triggers = list(dict.fromkeys(output.allowed_triggers)) or preserve_triggers
    intensity = output.motion_intensity if output.motion_intensity in _MOTION_INTENSITIES else "normal"
    return ActionAssetDesign(
        name=output.name,
        motion_description=output.motion_description,
        use_when=[item for item in output.use_when if item.strip()][:8],
        avoid_when=[item for item in output.avoid_when if item.strip()][:8],
        allowed_triggers=triggers,
        clip_kind=output.clip_kind,
        duration_seconds=output.duration_seconds,
        semantic_tags=tags,
        performance_role=_derived_role(output.clip_kind, output.performance_role),
        motion_intensity=intensity,
        interruptible=output.interruptible,
        reason=output.reason,
    )


def _details(design: ActionAssetDesign, language: str) -> list[DescribedDetail]:
    labels = _LABELS.get(language, _LABELS["zh"])
    items = [
        DescribedDetail(label=labels["name"], value=design.name),
        DescribedDetail(label=labels["motion"], value=design.motion_description),
    ]
    if design.use_when:
        items.append(DescribedDetail(label=labels["use_when"], value=" · ".join(design.use_when)[:600]))
    if design.avoid_when:
        items.append(DescribedDetail(label=labels["avoid_when"], value=" · ".join(design.avoid_when)[:600]))
    form = labels["loop"] if design.clip_kind == "loop" else labels["once"]
    items.append(DescribedDetail(label=labels["form"], value=f"{form} · {design.duration_seconds}{labels['seconds']}"))
    items.append(DescribedDetail(label=labels["intensity"], value=labels["intensities"][design.motion_intensity]))
    items.append(
        DescribedDetail(label=labels["interruptible"], value=labels["yes"] if design.interruptible else labels["no"])
    )
    return items


async def interpret_action_description(
    user_id: int, description: str, action_id: int | None = None
) -> ActionInterpretation:
    """把描述整理成完整动作定义；修改时保留描述未涉及的既有字段。"""

    async with SESSION_LOCAL() as db:
        language = resolve_language(await get_user_setting(db, user_id, "language"))
    labels = _LABELS.get(language, _LABELS["zh"])

    current: ActionAssetView | None = None
    if action_id is not None:
        async with SESSION_LOCAL() as db:
            current = await action_detail(db, user_id, action_id)
        if current is None:
            raise ActionNotFoundError(labels["not_found"])
        require_action_editable(current, current.updated_at)

    config = await load_user_llm_config(user_id, AiTaskKey.visuals)
    if not config.is_configured:
        raise ValueError(labels["unavailable"])

    payload = {
        "output_language": language,
        "semantic_tags": sorted(ACTION_SEMANTIC_TAGS),
        "user_description": description,
        "current_definition": (
            current.model_dump(
                include={
                    "name",
                    "motion_description",
                    "use_when",
                    "avoid_when",
                    "clip_kind",
                    "duration_seconds",
                    "semantic_tags",
                    "performance_role",
                    "motion_intensity",
                    "interruptible",
                }
            )
            if current is not None
            else None
        ),
    }

    output = await call_llm_json(
        config,
        ACTION_COMPOSE_TEXTS.get(language, ACTION_COMPOSE_TEXTS["zh"]),
        payload,
        schema=_ComposeOutput.model_json_schema(),
        schema_name="action_compose",
        parse_output=_ComposeOutput.model_validate,
        repair_prompt=ACTION_COMPOSE_REPAIR,
    )

    design = _design_from_output(output, current.allowed_triggers if current is not None else [])
    return ActionInterpretation(
        draft=design.model_dump(mode="json"),
        expected_updated_at=ensure_utc(current.updated_at) if current is not None else None,
        summary=design.name,
        details=_details(design, language),
    )
