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
    outfit_description: str = ""
    brief: str = ""
    notes: str = ""
    has_reference_image: bool = False


def _prompt_clause(value: str) -> str:
    return value.strip().rstrip("。.!！?？;； ")


def _identity_block(ctx: RoomPromptContext) -> str:
    appearance = _prompt_clause(ctx.appearance or "")
    identity = "图 1（全身参考图）" if ctx.has_reference_image else "全身参考图"
    block = f"{identity}用于确定角色身份与身材：保持同一角色的五官、肤色、物种、性别与身材比例；根据本次房间情境安排姿态与构图。"
    if appearance:
        return f"{block}外形文字仅作补充，与{identity}冲突时以其外貌为准：{appearance}。"
    return block


def build_room_prompt(ctx: RoomPromptContext) -> str:
    """身份图锁定外貌；用户图提供场景与所需姿势；穿着用着装描述原文。"""
    intent_value = ctx.intent.value if isinstance(ctx.intent, BackdropIntent) else str(ctx.intent)
    lighting = _INTENT_LIGHTING.get(intent_value, _INTENT_LIGHTING["decorate"])
    species = (ctx.species or "人类").strip() or "人类"
    parts = [
        f"16:9 写实室内环境图，{species}角色的私人起居房间，前后景分明。",
        "画面必须包含角色本人：全身或膝上构图，房间环境是视觉主体之一。角色动作与位置优先服从用户要求；未指定时自然安排。",
        _identity_block(ctx),
    ]
    if ctx.has_reference_image:
        parts.append(
            "图 2 是用户提供的场景参考：参考房间布局、家具、材质、色彩、光线与镜头视角，"
            "优先于下文的默认陈设与光线建议；与用户文字要求冲突时以文字要求为准。"
            "图 2 可以含有人物；用户要求模仿姿势时，让图 1 的角色采用相似的动作、朝向与画面位置，"
            "按该角色的物种与肢体结构自然调整，不受图 1 原姿势限制。"
            "不要复制图 2 人物的脸、身份、身材或穿着，也不要把其中的人物额外画进成品；"
            "最终只保留图 1 的角色，输出一个完整房间场景。",
        )
    outfit = _prompt_clause(ctx.outfit_description or "")
    if outfit:
        parts.append(
            f"角色当前穿着：{outfit}。服装、配色、发型与配饰以这段当前穿着描述为准，"
            "优先于所有参考图中的穿着，并在本次房间场景中保持这套搭配。",
        )
    elif ctx.has_reference_image:
        parts.append("角色穿着沿用图 1，不采用图 2 人物的服装、发型或配饰。")
    brief = _prompt_clause(ctx.brief or "")
    if brief:
        parts.append(f"房间陈设：{brief}。")
    parts.append(f"光线与色彩：{lighting}")
    notes = _prompt_clause(ctx.notes or "")
    if notes:
        parts.append(f"房间补充要求（优先于陈设与光线建议，不改变角色身份、当前穿着和画面规则）：{notes}。")
    parts.append(_HARD_RULES_ZH)
    return " ".join(parts)
