"""图生3D提示词组装引擎 — 确定性提示词构建器与LLM辅助工具。

整个图生 3D 管线（多视角生图 → 3D建模）中，全身提示词的各个组成部分来源与流转脉络如下：
                              ┌──────────────────────────────────────────────────────────┐
                              │                    1. Persona 数据库表                    │
                              │     (definition_json: appearance / personality / 物种)    │
                              └────────────────────────────┬─────────────────────────────┘
                                                           │
                      ┌────────────────────────────────────┴────────────────────────────────────┐
                      ▼                                                                         ▼
       ┌──────────────────────────────┐                                          ┌──────────────────────────────┐
       │   物种模板 & 骨骼姿态路由    │                                          │      角色身份与设定提取      │
       │ (resolve_fullbody_template)  │                                          │    (appearance/personality)  │
       └──────────────┬───────────────┘                                          └──────────────┬───────────────┘
                      │ (pose + view_features + flavor)                                         │ (外貌与性格设定)
                      │                                                                         │
                      └────────────────────────────┬────────────────────────────────────────────┘
                                                   │
                                                   ▼
┌──────────────────────────────┐        ┌───────────────────────────────────────────────────────┐
│     2. 风格路由与关键词      │ ────►  │              build_fullbody_prompt()                  │
│ (_FULLBODY_STYLE_WORDING)    │        │                                                       │
│ - cel_shading / anime_game_cg│        │ 拼接：[视角] + [姿态打底] + [视角特征] + [画幅棚拍] + │
└──────────────────────────────┘        │       [画风描述] + [角色设定] + [物种特效] + [反馈]   │
                                        └──────────────────────────┬────────────────────────────┘
                                                                   │ 组装完成的 Prompt 文本
                                                                   ▼
┌──────────────────────────────┐        ┌───────────────────────────────────────────────────────┐
│     3. Avatar 参考底图       │ ────►  │                 image_generation_tool                 │
│ (load_avatar_bytes_as_data_uri│       │                                                       │
│  首轮生成的半身头像作为参考) │        │ 发送至生图供应商 (MiniMax / Gemini / Grok)            │
└──────────────────────────────┘        └──────────────────────────┬────────────────────────────┘
                                                                   │ 生成种子图 (Front / Back)
                                                                   ▼
                                        ┌───────────────────────────────────────────────────────┐
                                        │                 Stage 2: 图生 3D 提交                 │
                                        │ (种子图打包上传至 Tripo3D 等服务商，不发送任何文本)   │
                                        └───────────────────────────────────────────────────────┘

对外公开的提示词构建方法
======================
enhance_avatar_prompt()   [LLM]      Persona 角色定义 → 半身头像图（bust avatar）提示词
build_fullbody_prompt()   [确定性]   视角(front/back) + 物种姿态模板 + 画风 + Persona 设定 → 全身立绘提示词

全身图提示词组装脉络与公式
=========================
最终生成的全身图提示词由以下 8 个部分按顺序组装而成：

  [1. 视角主谓前缀]       _VIEW_PREFIX[view]：正面 / 背面全身角色立绘。
  ＋
  [2. 姿态与体态模板]     template.pose：按物种与骨骼类型取自然站姿模板；双足姿态随画风路由——
                          2D 立绘画风（cel_shading）自然站姿（see-through 拆分不要求 A-pose），
                          3D 画风（anime_game_cg / realistic）A-pose（绑骨识别与多视角一致性）。
  ＋
  [3. 视角专项特征]       template.{view}_features：
                          - 正面：身体朝向正前方，正面视点。
                          - 背面：背面视点（角色转身180°背向镜头），展现背影、背部与后发细节，看不到正面面部。
  ＋
  [4. 画幅与影棚约束]     从头到脚完整可见，平视角度拍摄。纯白背景，均匀专业棚拍布光。
  ＋
  [5. 画风渲染规范]       _FULLBODY_STYLE_WORDING[style_id]：
                          - cel_shading（日系赛璐珞）：动漫角色立绘风格，清晰线条勾勒，分明纯净色块，自然流畅人体与平滑皮肤。
                          - anime_game_cg（二次元游戏CG）：次世代游戏 3D 角色渲染，全景全身建模质感，柔和次表面散射与自然肤质。
  ＋
  [6. 角色身份设定]       从 Persona.definition_json 提取 appearance 与 personality：
                          角色设定（外形特征：…；性格特点：…）。
  ＋
  [7. 物种特殊气质]       template.flavor（可选）：灵兽发光纹路、幻形虚幻粒子等（人类/精灵默认无）。
  ＋
  [8. 用户额外要求]       feedback（可选）：（要求：…）。

辅助工具说明：物种骨骼路由、视角名称映射、骨骼体态模板定义见下文各常量与类。
"""

import json
from dataclasses import dataclass, replace
from typing import Any, Literal

from components import safe_json_loads
from modules.companion import Persona, normalize_persona_aliases
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_client import MissingLlmConfigError, client_for_config, provider_for_service, provider_from_config
from .llm_retry import call_with_retry
from .providers import ProviderConfig, ServiceType, resolve_context_tokens, try_resolve
from .responses import build_responses_kwargs


def _strip_markdown_fence(raw: str) -> str:
    """剥离最外层 ```...``` 包装；只匹配首个开 fence 与字符串末尾的闭 fence，避免破坏 JSON 内的 ``` 子串。"""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1 and cleaned.endswith("```") and len(cleaned) > first_newline + 3:
            cleaned = cleaned[first_newline + 1 : -3].strip()
    return cleaned


def _persona_payload(persona: Persona) -> dict[str, str]:
    """回退到 ``{}`` 并归一化别名，使未填写完成的 persona 仍能产出 prompt。"""
    raw = getattr(persona, "definition_json", None) or "{}"
    data = safe_json_loads(raw, default={})
    return normalize_persona_aliases(data) if isinstance(data, dict) else {}


def _persona_visual_payload(persona: Persona, feedback: str | None) -> dict[str, str]:
    definition = _persona_payload(persona)
    return {
        "biological_type": definition.get("biological_type") or "",
        "gender": definition.get("gender") or "",
        "appearance": definition.get("appearance") or "",
        "personality": definition.get("personality") or "",
        "feedback": (feedback or "").strip(),
    }


async def chat(
    db: AsyncSession | None,
    user_id: int | None,
    system_prompt: str,
    user_payload: str,
    *,
    provider_config: ProviderConfig | None = None,
) -> str:
    """单次非流式 chat 往返；空内容视为错误，避免把空 prompt 透传给生图供应商。"""
    provider = (
        provider_from_config(provider_config)
        if provider_config is not None
        else await provider_for_service(db, user_id, "llm")
    )
    client = provider.raw_client()
    if client is None:
        raise MissingLlmConfigError(f"llm provider '{provider.provider_name}' does not expose the Responses API")
    request = build_responses_kwargs(
        model=provider.config.model,
        instructions=system_prompt,
        input_items=[{"role": "user", "content": [{"type": "input_text", "text": user_payload}]}],
    )
    response = await call_with_retry(client, **request)
    text = response.output_text.strip()
    if not text:
        raise RuntimeError("prompt enhancer returned an empty response")
    return text


async def call_llm_once(
    llm_cfg: dict[str, Any],
    system_prompt: str,
    user_payload: Any,
    *,
    max_output_tokens: int,
    reasoning_effort: str | None = None,
) -> str | None:
    """执行单次非流式调用；推理档位仅在当前供应商明确支持时下发。"""
    client = client_for_config(llm_cfg)
    provider_name = llm_cfg.get("provider_name", "")
    context_length = resolve_context_tokens(provider_name, ServiceType.llm)
    provider_cls = try_resolve(ServiceType.llm, provider_name)
    supported_efforts = getattr(provider_cls, "REASONING_EFFORTS", frozenset())
    reasoning = {"effort": reasoning_effort} if reasoning_effort in supported_efforts else None
    user_content = (
        json.dumps(user_payload, ensure_ascii=False) if isinstance(user_payload, dict | list) else str(user_payload)
    )
    request = build_responses_kwargs(
        model=llm_cfg["model_name"],
        instructions=system_prompt,
        input_items=[{"role": "user", "content": [{"type": "input_text", "text": user_content}]}],
        max_output_tokens=max_output_tokens,
        reasoning=reasoning,
    )
    resp = await call_with_retry(client, context_length=context_length, **request)
    return resp.output_text if resp else None


# 中文优先（persona 是中文，前端原生处理）；"纯白平面背景"使半身头像在浅色 UI 上可展示，且作为参考图干净 —— 下游不做色键。
_AVATAR_SYSTEM_PROMPT = (
    "你是一个专业的角色头像提示词工程师。你需要为角色生成一张高精度的半身头像图（avatar）提示词。\n"
    "\n"
    "输入字段：\n"
    "  - biological_type：物种；\n"
    "  - gender：性别；\n"
    "  - appearance：基础形象（脸型、体型、标志性细节等）；\n"
    "  - personality：性格；\n"
    "  - feedback：用户最近的反馈（可为空）。\n"
    "\n"
    "硬性要求：\n"
    "1. 半身特写（bust portrait），以「bust portrait of ...」开头；\n"
    "2. 重点呈现面部细节：脸型轮廓、五官比例、眼睛形状与瞳色瞳光、鼻子、嘴唇、眼神与神态、发型与发色质感；\n"
    "3. 服饰仅作自然背景，呈现简单、不遮蔽人物轮廓特征的服饰；\n"
    "4. 视角：正面朝向观众（front-facing bust portrait），平视镜头；\n"
    "5. 光线：柔和均匀的正面打光（soft even front lighting），无强烈阴影；\n"
    "6. 画风：photorealistic, hyperrealistic, ultra-detailed, natural skin texture, professional portrait photography, 8K；\n"
    "7. 必须包含「纯白平面背景，无场景、无渐变、无阴影」（浅色 UI 展示面与参考图干净度依赖此约束）；\n"
    "8. 全文使用中文，只保留专业术语与英文画风关键词；\n"
    "9. appearance 与 feedback 中的用户原始描述承载明确意图，其中具体的颜色、发型、五官、风格等细节必须忠实保留进最终 prompt，不得改写、泛化或遗漏（例如「深棕色头发带银色挑染」必须逐字体现「深棕色头发」与「银色挑染」，不可简化为「深色头发」）。feedback 的修改指令优先级最高，用于覆盖之前的 appearance 描述。若用户上传了参考图，提取角色的核心外观特征即可，不要过分在意参考图中的细节（如不需要和用户上传图像的动作、姿态一致，保持标准正面半身像）；\n"
    "10. 不要解释、不要寒暄，直接输出最终中文 prompt 文本。"
)


async def enhance_avatar_prompt(
    db: AsyncSession | None,
    user_id: int | None,
    persona: Persona,
    *,
    feedback: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> str:
    """把 persona 定义改写为一段聚焦的中文半身头像（bust）prompt；结果写入 ``AvatarAsset.avatar_prompt``，供 ``build_fullbody_prompt`` 作为身份锚点保证全身图与头像视觉一致。"""
    payload = _persona_visual_payload(persona, feedback)
    user_payload = (
        f"请根据以下角色定义生成半身头像图的提示词：\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    )
    raw = await chat(db, user_id, _AVATAR_SYSTEM_PROMPT, user_payload, provider_config=provider_config)
    return _strip_markdown_fence(raw)


FullbodyStyle = Literal["cel_shading", "anime_game_cg", "realistic"]

# 预设物种直接带风格；自定义物种由 LLM 人脸判定路由（见 ``rig_type_selector.classify_species``）
_SPECIES_STYLE: dict[str, FullbodyStyle] = {
    "人类": "anime_game_cg",
    "精灵": "anime_game_cg",
    "机甲": "realistic",
    "灵兽": "realistic",
    "幻形": "realistic",
}

# 骨骼预设物种：固定体型，无需 LLM 骨骼分类
_PRESET_SPECIES: frozenset[str] = frozenset({"人类", "精灵", "机甲"})


def resolve_fullbody_style(species: str, has_humanoid_face: bool | None = None) -> FullbodyStyle:
    """根据物种解析 3D 风格路由：类人物种走 CG 风格（anime_game_cg），非人物种走写实风格（realistic）。"""
    preset = _SPECIES_STYLE.get(species.strip())
    if preset is not None:
        return preset
    return "realistic" if has_humanoid_face is False else "anime_game_cg"


def is_preset_species(species: str) -> bool:
    """若物种拥有固定体型（无需骨骼类型分类）则返回 True。"""
    return species in _PRESET_SPECIES


_FULLBODY_STYLE_WORDING: dict[str, str] = {
    "cel_shading": "日系赛璐珞动漫角色立绘风格（cel-shading anime character art），清晰利落的线条勾勒与分明纯净的阴影色块，明亮通透的色彩，自然流畅的人体线条与平滑细腻的皮肤，纯净清爽的面部与发丝结构。",
    "anime_game_cg": "次世代二次元游戏CG风格（anime game 3D CGI），原神与崩铁级现代3D二次元角色建模质感，全景全身立绘，精致立体的角色形体与层次分明的发束，柔和通透的次表面散射与自然肤质，微立体阴影与平滑材质，8K超清。",
    "realistic": "写实摄影质感与细腻真实的材质光影风格（photorealistic, hyperrealistic），真实自然的生物肌理、毛发与材质纹理，高精度棚拍光影与层次，8K超清。",
}


@dataclass(frozen=True)
class FullbodyTemplate:
    front_features: str
    back_features: str
    pose: str
    flavor: str = ""
    rig_type: str = "biped"
    style: str = "cel_shading"


_BIPED_A_POSE = "标准A-pose站姿，身体直立，双臂自然向身体两侧微张45度，手臂与躯干自然分开，手肘微屈，手指自然舒展，双腿直立，双脚分开与肩同宽。"
_BIPED_NATURAL_POSE = (
    "自然站姿，身体放松直立，双臂自然垂于身体两侧并微微离开躯干，手指自然舒展，双腿直立，双脚自然分开与肩同宽。"
)

_BIPED_HUMANOID_TEMPLATE = FullbodyTemplate(
    front_features="身体朝向正前方，正面视点。",
    back_features="背面视点（角色转身180°背向镜头），展现背影、背部与后发细节，看不到正面面部。",
    pose=_BIPED_A_POSE,
    rig_type="biped",
)
_SPECIES_TEMPLATES: dict[str, FullbodyTemplate] = {
    "人类": _BIPED_HUMANOID_TEMPLATE,
    "精灵": _BIPED_HUMANOID_TEMPLATE,
    "机甲": FullbodyTemplate(
        front_features="机体朝向正前方，正面视点。",
        back_features="背面视点（机体转身180°背向镜头），机体后背与推进器结构清晰，看不到正面面部。",
        pose=_BIPED_A_POSE,
        rig_type="biped",
        style="cel_shading",
    ),
}

_SPECIES_FLAVOR: dict[str, str] = {
    "灵兽": "角色散发灵气与神秘气场，身上可能有发光纹路、灵力标记或神秘图腾。",
    "幻形": "角色呈现虚幻、流变的气质，身体边缘可能有半透明、发光或粒子消散效果。",
}

_RIG_TYPE_TEMPLATES: dict[str, FullbodyTemplate] = {
    "biped": _SPECIES_TEMPLATES["人类"],
    "quadruped": FullbodyTemplate(
        front_features="正前方视点，身体朝前。",
        back_features="背面视点（转身180°），身体背部与尾部清晰，看不到面部。",
        pose="四足自然直立站立，四腿分开；脊椎水平，头抬起；尾巴自然舒展。",
        rig_type="quadruped",
    ),
    "avian": FullbodyTemplate(
        front_features="正前方视点，胸腹部与面部朝前。",
        back_features="背面视点（转身180°），背部羽毛与双翼背侧清晰，看不到面部。",
        pose="双足直立站立，双翼向两侧半展约30-45度；身体直立。",
        rig_type="avian",
    ),
    "serpentine": FullbodyTemplate(
        front_features="",
        back_features="背面，脊背纹理连贯至尾尖。",
        pose="身体水平自然伸展或S形蜿蜒，全身完整可见；头部抬起。",
        rig_type="serpentine",
    ),
    "aquatic": FullbodyTemplate(
        front_features="",
        back_features="背面，背鳍与尾鳍形态清晰。",
        pose="身体水平伸展，各鱼鳍完全展开；尾鳍自然伸展。",
        rig_type="aquatic",
    ),
    "hexapod": FullbodyTemplate(
        front_features="",
        back_features="背面，背甲纹理清晰。",
        pose="六足自然直立站立，六腿对称分开；各体段完整可见。",
        rig_type="hexapod",
    ),
    "octopod": FullbodyTemplate(
        front_features="",
        back_features="背面，背甲轮廓清晰。",
        pose="八足对称展开于身体两侧，每条腿清晰可辨；身体居中。",
        rig_type="octopod",
    ),
}


def resolve_fullbody_template(species: str, rig_type: str = "biped", style: str = "cel_shading") -> FullbodyTemplate:
    """解析完整的全身图模板。双足姿态随画风路由：2D 立绘画风（cel_shading）走自然站姿——
    see-through 拆分不要求 A-pose；3D 画风（anime_game_cg / realistic）保持 A-pose 供绑骨识别与多视角一致性。"""
    if species in _SPECIES_TEMPLATES:
        template = _SPECIES_TEMPLATES[species]
    else:
        flavor = _SPECIES_FLAVOR.get(species, "")
        template = _RIG_TYPE_TEMPLATES.get(rig_type, _RIG_TYPE_TEMPLATES["biped"])
        if flavor:
            template = replace(template, flavor=flavor)
    if template.rig_type == "biped":
        template = replace(template, pose=_BIPED_NATURAL_POSE if style == "cel_shading" else _BIPED_A_POSE)
    return template if template.style == style else replace(template, style=style)


_VIEW_PREFIX = {"front": "正面全身角色立绘", "back": "背面全身角色立绘"}


def build_fullbody_prompt(
    view: str,
    *,
    template: FullbodyTemplate,
    style_id: str | None = None,
    feedback: str | None = None,
    appearance: str = "",
    personality: str = "",
    avatar_prompt: str = "",
    persona: Persona | dict | None = None,
) -> str:
    """为某个视角拼装一条生图 prompt（无 LLM 往返）；由 ``application/generation/avatar_service`` 按视角调用。全身图由外貌设定、性格特点、画风词典与用户额外要求装配，外形特征由原参考图/头像锚定，不带入头像阶段特异性的 avatar_prompt。"""
    style_key = style_id or template.style or "cel_shading"
    style_wording = _FULLBODY_STYLE_WORDING.get(style_key, _FULLBODY_STYLE_WORDING["cel_shading"])
    features = getattr(template, f"{view}_features", "")

    if persona is not None:
        definition = persona if isinstance(persona, dict) else _persona_payload(persona)
        if not appearance:
            appearance = str(definition.get("appearance") or "").strip()
        if not personality:
            personality = str(definition.get("personality") or "").strip()

    prompt = f"{_VIEW_PREFIX.get(view, '正面全身角色立绘')}，{template.pose}{features}从头到脚完整可见，平视角度拍摄。纯白背景，均匀专业棚拍布光。{style_wording}"

    identity_parts: list[str] = []
    if appearance and appearance.strip():
        identity_parts.append(f"外形特征：{appearance.strip()}")
    if personality and personality.strip():
        identity_parts.append(f"性格特点：{personality.strip()}")

    if identity_parts:
        prompt += f"角色设定（{'；'.join(identity_parts)}）。"

    if template.flavor:
        prompt += template.flavor
    if feedback and feedback.strip():
        prompt += f"（要求：{feedback.strip()}）"
    return prompt


# 换装约束：五官/物种/性别锁定，服装/发型/配饰可换（DESIGN §5.4 锁定豁免——可换元素而非身份变更）
_OUTFIT_CHANGE_CLAUSE = "换装要求：与第一张参考图（头像）中的角色保持完全相同的身份——五官、脸型、体型、物种与性别严格一致，不得改变；仅重新设计服装、发型与配饰，按下方着装要求呈现。"


def build_outfit_prompt(
    *,
    template: FullbodyTemplate,
    style_id: str | None = None,
    feedback: str,
    appearance: str = "",
    personality: str = "",
) -> str:
    """换装立绘 prompt：在正面全身 prompt 之上叠加「锁身份、换穿着」约束；身份由主参考图（头像种子图）锚定，着装要求进 feedback 槽。"""
    base = build_fullbody_prompt(
        "front",
        template=template,
        style_id=style_id,
        appearance=appearance,
        personality=personality,
    )
    return f"{base}{_OUTFIT_CHANGE_CLAUSE}（着装要求：{feedback.strip()}）"
