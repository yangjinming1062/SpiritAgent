"""房间图提示词组装。

发给生图模型的句子只描述它能看见的东西：参考图是像素，不是产品术语，也读不出性格。
穿着来自当前外观的着装描述原文。
brief 由内部小模型根据性格 / 意图写成房间陈设文案；最终 prompt 由 brief + 模板拼出，不再二次 LLM 调用。
"""

from dataclasses import dataclass

from modules.companion import BackdropIntent

_HARD_RULES_ZH = "画面必须包含这个人（全身或膝上，自然坐 / 靠窗 / 站在房间一侧），不要大头特写；禁止工作台 / IDE / 终端 / 屏幕 UI / 对话框 / 水印 / 可读文字 / 商标 / 第二个人。"

# intent 决定光线与氛围关键词；不需要 LLM 二次装配。
_INTENT_LIGHTING: dict[str, str] = {
    "decorate": "温暖自然光，午后斜阳，色彩鲜明。",
    "seasonal": "符合季节的氛围光（春樱 / 夏雨 / 秋叶 / 冬雪）。",
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


def _identity_block(ctx: RoomPromptContext) -> str:
    species = (ctx.species or "人类").strip() or "人类"
    appearance = (ctx.appearance or "").strip()
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
        f"16:9 室内中远景，写实摄影，{species}的私人起居房间。前后景分明。",
        _HARD_RULES_ZH,
        _identity_block(ctx),
    ]
    outfit = (ctx.outfit_description or "").strip()
    if ctx.has_outfit_ref:
        parts.append(
            "参考图右侧是这个人当前的全身穿着：必须保持相同服装、配色、发型、配饰与身体特征，不得自行换装。",
        )
    if outfit:
        parts.append(outfit)
    parts.append(f"光线：{lighting}")
    brief = (ctx.brief or "").strip()
    if brief:
        parts.append(f"房间陈设：{brief}")
    notes = (ctx.notes or "").strip()
    if notes:
        parts.append(f"补充：{notes}")
    return " ".join(parts)
