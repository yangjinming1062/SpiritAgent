"""房间图提示词组装。

发给生图模型的句子只描述它能看见的东西：参考图是像素，不是产品术语，也读不出性格。
穿着来自当前外观的着装描述原文。
brief 由内部小模型根据性格 / 意图写成陈设建议；明确要求独立保留，优先于陈设与光线建议。
最终 prompt 确定性组装，不再二次 LLM 调用。
"""

from dataclasses import dataclass

from modules.companion import BackdropIntent

_HARD_RULES_ZH = (
    "只出现这一位角色，不得增加第二个人或人形主体。不要头像特写、拼贴或参考图版式；"
    "不要工作台、IDE、终端、屏幕 UI、对话框、边框、可读文字、商标或水印。"
)

# intent 决定光线与氛围关键词；不需要 LLM 二次装配。
_INTENT_LIGHTING: dict[str, str] = {
    "decorate": "温暖自然光，午后斜阳，色彩鲜明。",
    "seasonal": "与房间简述中的季节一致的自然氛围光；未指定具体季节时使用温和自然光。",
    "mood": "低饱和与柔光，与心情呼应。",
    "rebuild": "明亮的自然光。",
}


@dataclass(frozen=True)
class RoomPromptContext:
    species: str
    appearance: str
    intent: BackdropIntent | str
    has_identity_ref: bool
    has_outfit_ref: bool = False
    outfit_description: str = ""
    brief: str = ""
    notes: str = ""


def _prompt_clause(value: str) -> str:
    return value.strip().rstrip("。.!！?？;； ")


def _identity_block(ctx: RoomPromptContext) -> str:
    species = (ctx.species or "人类").strip() or "人类"
    appearance = _prompt_clause(ctx.appearance or "")
    if ctx.has_identity_ref:
        reference_position = "参考图左侧" if ctx.has_outfit_ref else "参考图"
        block = f"{reference_position}是这个人的正面半身像：只用来锁定五官、发型、肤色、物种与性别，必须画成同一个人，不要换成另一张脸。"
        if appearance:
            return f"{block}外形文字仅作补充，与参考图冲突时以参考图的外貌为准：{appearance}。"
        return block
    if appearance:
        return f"按这段外形描述画出这个人：{appearance}。"
    return f"画面中的人是{species}，外貌自然可信。"


def build_room_prompt(ctx: RoomPromptContext) -> str:
    """组装最终房间图生图 prompt。参考图只锁定外貌；穿着用着装描述原文；性格不进本函数。"""
    intent_value = ctx.intent.value if isinstance(ctx.intent, BackdropIntent) else str(ctx.intent)
    lighting = _INTENT_LIGHTING.get(intent_value, _INTENT_LIGHTING["decorate"])
    species = (ctx.species or "人类").strip() or "人类"
    parts = [
        f"16:9 写实室内环境图，{species}角色的私人起居房间，前后景分明。",
        "画面必须包含角色本人：全身或膝上构图，角色自然坐着、靠窗或站在房间一侧；房间环境是视觉主体之一。",
        _identity_block(ctx),
    ]
    outfit = _prompt_clause(ctx.outfit_description or "")
    if ctx.has_outfit_ref:
        outfit_reference = "参考图右侧" if ctx.has_identity_ref else "参考图"
        parts.append(
            f"{outfit_reference}是这个人当前的全身穿着：必须保持相同服装、配色、发型、配饰与身体特征，不得自行换装。",
        )
    if outfit:
        parts.append(f"角色当前穿着：{outfit}。不要因房间主题改变这套穿着。")
    brief = _prompt_clause(ctx.brief or "")
    if brief:
        parts.append(f"房间陈设：{brief}。")
    parts.append(f"光线与色彩：{lighting}")
    notes = _prompt_clause(ctx.notes or "")
    if notes:
        parts.append(f"房间补充要求（优先于陈设与光线建议，不改变角色身份、当前穿着和画面规则）：{notes}。")
    parts.append(_HARD_RULES_ZH)
    return " ".join(parts)
