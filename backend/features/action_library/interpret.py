"""把用户的一句日常描述整理成动作定义草稿；只产出草稿，不登记、不评审。"""

from typing import Any

from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, resolve_language
from pydantic import BaseModel, ConfigDict, Field

from features.configuration.settings.values import get_user_setting
from features.media.described import DescribedDetail
from features.media.visual_semantics import (
    ACTION_SEMANTIC_TAGS,
    ActionMotionIntensity,
    ActionPerformanceRole,
    SemanticTag,
)

from .prompts import ACTION_COMPOSE_REPAIR, ACTION_COMPOSE_TEXTS
from .queries import action_detail
from .schemas import (
    ActionAssetDesign,
    ActionAssetView,
    ActionClipKind,
    ActionCondition,
    ActionInterpretation,
    ActionTrigger,
)
from .service import require_action_editable

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
        "tags": "语义标签",
        "tag_values": {
            "idle": "安静相伴",
            "attention": "注意用户",
            "interaction": "互动回应",
            "reading": "阅读",
            "working": "专注做事",
            "creating": "创作",
            "thinking": "思考",
            "resting": "休息",
            "organizing": "整理",
            "socializing": "社交",
            "stretching": "舒展",
            "grooming": "整理仪容",
            "drinking": "饮水",
            "dancing": "舞蹈",
        },
        "triggers": "触发方式",
        "trigger_values": {"chat": "对话中", "manual": "手动", "periodic": "定时", "user_return": "回来时"},
        "role": "表现角色",
        "roles": {"idle": "常驻待机", "sustained": "持续状态", "reaction": "即时反应"},
        "intensity": "幅度",
        "intensities": {"subtle": "轻微", "normal": "日常", "expressive": "明显"},
        "interruptible": "可被打断",
        "yes": "是",
        "no": "否",
        "cleared": "已清空",
        "reason_basis": "整理依据",
        "unavailable": "整理描述所需的模型服务尚未配置，请联系管理员。",
        "not_found": "未找到该动作素材",
        "no_change": "没有识别出要修改的内容，请换一种说法再试试。",
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
        "tags": "Semantic tags",
        "tag_values": {
            "idle": "Quiet company",
            "attention": "Attention",
            "interaction": "Interaction",
            "reading": "Reading",
            "working": "Focus",
            "creating": "Creating",
            "thinking": "Thinking",
            "resting": "Resting",
            "organizing": "Organizing",
            "socializing": "Socializing",
            "stretching": "Stretching",
            "grooming": "Grooming",
            "drinking": "Drinking",
            "dancing": "Dancing",
        },
        "triggers": "Triggers",
        "trigger_values": {"chat": "In chat", "manual": "Manual", "periodic": "Scheduled", "user_return": "On return"},
        "role": "Performance role",
        "roles": {"idle": "Ambient idle", "sustained": "Sustained state", "reaction": "Immediate reaction"},
        "intensity": "Intensity",
        "intensities": {"subtle": "Subtle", "normal": "Normal", "expressive": "Expressive"},
        "interruptible": "Interruptible",
        "yes": "Yes",
        "no": "No",
        "cleared": "Cleared",
        "reason_basis": "Basis",
        "unavailable": "The model service needed to interpret descriptions is not configured.",
        "not_found": "Action material not found.",
        "no_change": "No change to apply was identified. Describe the change differently.",
    },
}


class ActionNotFoundError(ValueError):
    """草稿指向的动作不存在或不属于当前账户。"""


class _ComposePatch(BaseModel):
    """修改已有动作时的变化字段；null 表示保持原值，由程序按原定义保留。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=64)
    motion_description: str | None = Field(default=None, min_length=10, max_length=600)
    use_when: list[ActionCondition] | None = Field(default=None, max_length=8)
    avoid_when: list[ActionCondition] | None = Field(default=None, max_length=8)
    clip_kind: ActionClipKind | None = None
    duration_seconds: int | None = Field(default=None, ge=1, le=15)
    semantic_tags: list[SemanticTag] | None = Field(default=None, max_length=8)
    allowed_triggers: list[ActionTrigger] | None = Field(default=None, max_length=8)
    performance_role: ActionPerformanceRole | None = None
    motion_intensity: ActionMotionIntensity | None = None
    interruptible: bool | None = None
    reason: str | None = Field(default=None, max_length=400)


class _CreateOutput(_ComposePatch):
    """新建动作必须给出名称与动作描述，其余维度缺省时按契约默认值补齐。"""

    name: str = Field(min_length=1, max_length=64)
    motion_description: str = Field(min_length=10, max_length=600)


def _derived_role(clip_kind: ActionClipKind, role: ActionPerformanceRole | None) -> ActionPerformanceRole:
    """复用后端既有的播放方式与表现用途约束，联动变化收敛为合法组合。"""
    if role is None:
        return "sustained" if clip_kind == "loop" else "reaction"
    if clip_kind == "loop" and role == "reaction":
        return "sustained"
    if clip_kind == "once" and role in {"idle", "sustained"}:
        return "reaction"
    return role


def _design_from_output(output: _ComposePatch, baseline: ActionAssetView | None) -> ActionAssetDesign:
    """合并成最终定义：只写入模型给出的变化，其余维度按原定义确定性保留。"""
    base = baseline.model_dump(include=set(ActionAssetDesign.model_fields)) if baseline else {}
    merged = base | output.model_dump(exclude_none=True)
    merged["reason"] = merged.get("reason") or base.get("reason", "")
    clip_kind = merged.setdefault("clip_kind", "once")
    merged["performance_role"] = _derived_role(clip_kind, merged.get("performance_role"))
    return ActionAssetDesign.model_validate(merged)


def _changed_fields(design: ActionAssetDesign, baseline: ActionAssetView) -> set[str]:
    """对比合并结果与原定义，只返回真正变化的维度。"""
    return {
        field
        for field, value, original in (
            ("name", design.name, baseline.name),
            ("motion_description", design.motion_description, baseline.motion_description),
            ("use_when", design.use_when, baseline.use_when),
            ("avoid_when", design.avoid_when, baseline.avoid_when),
            ("allowed_triggers", design.triggers, baseline.allowed_triggers),
            ("clip_kind", design.clip_kind, baseline.clip_kind),
            ("duration_seconds", design.duration_seconds, baseline.duration_seconds),
            ("semantic_tags", design.semantic_tags, baseline.semantic_tags),
            ("performance_role", design.performance_role, baseline.performance_role),
            ("motion_intensity", design.motion_intensity, baseline.motion_intensity),
            ("interruptible", design.interruptible, baseline.interruptible),
            ("reason", design.reason, baseline.reason),
        )
        if value != original
    }


def _details(design: ActionAssetDesign, language: str, changes: set[str] | None = None) -> list[DescribedDetail]:
    """新建展示完整定义，空条件不列；修改只展示相对原定义实际变化的维度。"""
    labels = _LABELS.get(language, _LABELS["zh"])

    def show(field: str) -> bool:
        return changes is None or field in changes

    def list_value(values: list[str]) -> str:
        return " · ".join(values)[:600] or labels["cleared"]

    items: list[DescribedDetail] = []
    if show("name"):
        items.append(DescribedDetail(label=labels["name"], value=design.name))
    if show("motion_description"):
        items.append(DescribedDetail(label=labels["motion"], value=design.motion_description))
    if show("use_when") and (design.use_when or changes is not None):
        items.append(DescribedDetail(label=labels["use_when"], value=list_value(design.use_when)))
    if show("avoid_when") and (design.avoid_when or changes is not None):
        items.append(DescribedDetail(label=labels["avoid_when"], value=list_value(design.avoid_when)))
    if show("clip_kind") or show("duration_seconds"):
        form = labels["loop"] if design.clip_kind == "loop" else labels["once"]
        items.append(
            DescribedDetail(label=labels["form"], value=f"{form} · {design.duration_seconds}{labels['seconds']}")
        )
    if show("semantic_tags") and (design.semantic_tags or changes is not None):
        tags = " · ".join(labels["tag_values"][tag] for tag in design.semantic_tags) or labels["cleared"]
        items.append(DescribedDetail(label=labels["tags"], value=tags))
    if show("allowed_triggers"):
        triggers = " · ".join(labels["trigger_values"][trigger] for trigger in design.triggers)
        items.append(DescribedDetail(label=labels["triggers"], value=triggers))
    if show("performance_role"):
        items.append(DescribedDetail(label=labels["role"], value=labels["roles"][design.performance_role]))
    if show("motion_intensity"):
        items.append(DescribedDetail(label=labels["intensity"], value=labels["intensities"][design.motion_intensity]))
    if show("interruptible"):
        items.append(
            DescribedDetail(
                label=labels["interruptible"], value=labels["yes"] if design.interruptible else labels["no"]
            )
        )
    if design.reason and show("reason"):
        items.append(DescribedDetail(label=labels["reason_basis"], value=design.reason))
    return items


async def interpret_action_description(
    user_id: int, description: str, action_id: int | None = None
) -> ActionInterpretation:
    """新建整理出完整定义；修改以原定义为基线，只应用描述明确要求的变化。"""

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
            current.model_dump(include=set(ActionAssetDesign.model_fields)) if current is not None else None
        ),
    }

    contract = _CreateOutput if current is None else _ComposePatch
    output = await call_llm_json(
        config,
        ACTION_COMPOSE_TEXTS.get(language, ACTION_COMPOSE_TEXTS["zh"]),
        payload,
        schema=contract.model_json_schema(),
        schema_name="action_compose",
        parse_output=contract.model_validate,
        repair_prompt=ACTION_COMPOSE_REPAIR,
    )

    design = _design_from_output(output, current)
    changes = _changed_fields(design, current) if current is not None else None
    if changes is not None and not changes:
        raise ValueError(labels["no_change"])
    return ActionInterpretation(
        draft=design.model_dump(mode="json"),
        expected_updated_at=ensure_utc(current.updated_at) if current is not None else None,
        summary=design.name,
        details=_details(design, language, changes),
    )
