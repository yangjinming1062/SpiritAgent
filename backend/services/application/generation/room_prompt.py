"""房间图提示词组装。

发给生图模型的句子只描述它能看见的东西：参考图是像素，不是产品术语，也读不出性格。
穿着来自当前外观的着装描述原文。
brief 由内部小模型根据性格 / 意图写成陈设建议；明确要求独立保留，优先于陈设与光线建议。
最终 prompt 确定性组装，不再二次 LLM 调用。

onboarding 完成后全身种子图恒在：AI 路径与自备图路径都以该图为身份参考，不存在纯文字变体。
"""

from dataclasses import dataclass

from modules.companion import BackdropIntent
from prompts.generation import (
    HARD_RULES_ZH,
    INTENT_LIGHTING,
    ROOM_BRIEF_TEMPLATE,
    ROOM_KEEP_OUTFIT_TEMPLATE,
    ROOM_LIGHTING_TEMPLATE,
    ROOM_NOTES_TEMPLATE,
    ROOM_OUTFIT_TEMPLATE,
    ROOM_SCENE_REFERENCE,
    ROOM_SCENE_TEMPLATE,
)


@dataclass(frozen=True)
class RoomPromptContext:
    intent: BackdropIntent | str
    outfit_description: str = ""
    brief: str = ""
    notes: str = ""
    # True：另有用户场景图（图 2），全身种子为图 1；False：仅有全身种子参考图。
    has_reference_image: bool = False


def _prompt_clause(value: str) -> str:
    return value.strip().rstrip("。.!！?？;； ")


def build_room_prompt(ctx: RoomPromptContext) -> str:
    """房间以空间描述为主，角色身份只由参考图表达；当前造型独立装配。"""
    intent_value = ctx.intent.value if isinstance(ctx.intent, BackdropIntent) else str(ctx.intent)
    lighting = INTENT_LIGHTING.get(intent_value, INTENT_LIGHTING["decorate"])
    reference = "图 1" if ctx.has_reference_image else "参考图"
    parts = [ROOM_SCENE_TEMPLATE.format(reference=reference)]
    if ctx.has_reference_image:
        parts.append(ROOM_SCENE_REFERENCE)
    outfit = _prompt_clause(ctx.outfit_description or "")
    parts.append(
        ROOM_OUTFIT_TEMPLATE.format(outfit=outfit) if outfit else ROOM_KEEP_OUTFIT_TEMPLATE.format(reference=reference),
    )
    brief = _prompt_clause(ctx.brief or "")
    if brief:
        parts.append(ROOM_BRIEF_TEMPLATE.format(brief=brief))
    parts.append(ROOM_LIGHTING_TEMPLATE.format(lighting=lighting))
    notes = _prompt_clause(ctx.notes or "")
    if notes:
        parts.append(ROOM_NOTES_TEMPLATE.format(notes=notes))
    parts.append(HARD_RULES_ZH)
    return "\n".join(parts)
