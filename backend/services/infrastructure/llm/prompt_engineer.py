"""图生3D提示词组装引擎 — 确定性提示词构建器与LLM辅助工具。

数据流转：Persona 表（definition_json）→ 物种模板与骨骼姿态路由（resolve_fullbody_template）
与角色身份设定提取（appearance / personality）→ build_fullbody_prompt() 拼接
[画风描述] + [角色设定] + [物种特效] + [反馈] → image_generation_tool 发送至生图供应商
（MiniMax / Gemini / Grok）生成种子图（Front / Back）→ Stage 2: 图生 3D 提交（种子图打包
上传至 Tripo3D 等服务商，不发送任何文本）。

对外公开的提示词构建方法：
- enhance_avatar_prompt()   [LLM]      Persona 角色定义 → 半身头像图（bust avatar）提示词
- describe_garment_image()  [LLM]      用户服装参考图与文字要求 → 整合的着装设计稿（换装参考不直传生图，生图恒单参考）
- build_fullbody_prompt()   [确定性]   视角(front/back) + 物种姿态模板 + 画风 + Persona 设定 → 全身立绘提示词

全身图提示词按稳定优先级组装：视角与主体 → 物种骨骼姿势 → 完整画幅 → 参考图身份锚点 →
渲染风格 → Persona 外观与克制气质 → 物种特效 → 不冲突的用户反馈 → 纯白背景与排除项。
双足 2D 立绘采用自然站姿；3D 种子采用 A-pose 以利绑骨与多视角一致性。

辅助工具说明：物种骨骼路由、视角名称映射、骨骼体态模板定义见下文各常量与类。
"""

import json
from dataclasses import dataclass, replace
from typing import Any, Literal

from components import SESSION_LOCAL, safe_json_loads
from modules.companion import Persona
from prompts.generation import (
    AVATAR_SYSTEM_PROMPT,
    BIPED_A_POSE,
    BIPED_NATURAL_POSE,
    FULLBODY_STYLE_WORDING,
    GARMENT_DESCRIBE_SYSTEM,
    OUTFIT_CHANGE_CLAUSE,
    SELF_SOURCE_REFERENCE_CLAUSE,
    SELF_SOURCE_REWRITE_LEAD,
    VIEW_PREFIX,
    IdentityAnchor,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_client import (
    MissingLlmConfigError,
    client_for_config,
    provider_for_service,
    provider_from_config,
    resolve_vision_chain,
)
from .llm_retry import call_with_retry
from .providers import ProviderConfig, ServiceType, resolve_context_tokens, try_resolve
from .responses import build_responses_kwargs

FullbodyStyle = Literal["refined_anime_cg", "realistic"]

# 预设物种直接带风格；自定义物种由 LLM 人脸判定路由（见 ``rig_type_selector.classify_species``）
_SPECIES_STYLE: dict[str, FullbodyStyle] = {
    "人类": "refined_anime_cg",
    "精灵": "refined_anime_cg",
    "机甲": "realistic",
    "灵兽": "realistic",
    "幻形": "realistic",
}

# 骨骼预设物种：固定体型，无需 LLM 骨骼分类
_PRESET_SPECIES: frozenset[str] = frozenset({"人类", "精灵", "机甲"})


@dataclass(frozen=True)
class FullbodyTemplate:
    front_features: str
    back_features: str
    pose: str
    flavor: str = ""
    rig_type: str = "biped"
    style: str = "refined_anime_cg"


_BIPED_HUMANOID_TEMPLATE = FullbodyTemplate(
    front_features="身体朝向正前方，正面视点。",
    back_features="背面视点（角色转身180°背向镜头），展现背影、背部与后发细节，看不到正面面部。",
    pose=BIPED_A_POSE,
    rig_type="biped",
)
_SPECIES_TEMPLATES: dict[str, FullbodyTemplate] = {
    "人类": _BIPED_HUMANOID_TEMPLATE,
    "精灵": _BIPED_HUMANOID_TEMPLATE,
    "机甲": FullbodyTemplate(
        front_features="机体朝向正前方，正面视点。",
        back_features="背面视点（机体转身180°背向镜头），机体后背与推进器结构清晰，看不到正面面部。",
        pose=BIPED_A_POSE,
        rig_type="biped",
        style="refined_anime_cg",
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


def build_image_edit_prompt(feedback: str, *, preserve: str) -> str:
    """图像编辑 prompt：输入图是编辑底图（上一版产物），只按用户本次反馈做增量修改。"""
    clause = _prompt_clause(feedback)
    if not clause:
        raise ValueError("image edit requires non-empty feedback")
    return (
        "对输入图片做编辑，不是重新创作。用户本次的修改要求："
        f"{clause}。只修改与该要求直接相关的部分，其余内容保持原样。{preserve}。"
    )


def _strip_markdown_fence(raw: str) -> str:
    """剥离最外层 ```...``` 包装；只匹配首个开 fence 与字符串末尾的闭 fence，避免破坏 JSON 内的 ``` 子串。"""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1 and cleaned.endswith("```") and len(cleaned) > first_newline + 3:
            cleaned = cleaned[first_newline + 1 : -3].strip()
    return cleaned


def _prompt_clause(value: str) -> str:
    """把外部描述收成可嵌入句子的片段，避免装配后出现重复句号。"""
    return value.strip().rstrip("。.!！?？;； ")


def _persona_payload(persona: Persona) -> dict[str, str]:
    """回退到 ``{}``，使未填写完成的 persona 仍能产出 prompt。"""
    raw = getattr(persona, "definition_json", None) or "{}"
    data = safe_json_loads(raw, default={})
    return data if isinstance(data, dict) else {}


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
    user_payload = json.dumps(payload, ensure_ascii=False)
    raw = await chat(db, user_id, AVATAR_SYSTEM_PROMPT, user_payload, provider_config=provider_config)
    return _strip_markdown_fence(raw)


def resolve_fullbody_style(species: str, has_humanoid_face: bool | None = None) -> FullbodyStyle:
    """根据物种解析 3D 种子画风路由：类人物种走精绘画风（refined_anime_cg，与 2D 立绘一致），非人物种走写实风格（realistic）。"""
    preset = _SPECIES_STYLE.get(species.strip())
    if preset is not None:
        return preset
    return "realistic" if has_humanoid_face is False else "refined_anime_cg"


def is_preset_species(species: str) -> bool:
    """若物种拥有固定体型（无需骨骼类型分类）则返回 True。"""
    return species in _PRESET_SPECIES


def resolve_fullbody_template(
    species: str,
    rig_type: str = "biped",
    style: str = "refined_anime_cg",
    *,
    a_pose: bool = False,
) -> FullbodyTemplate:
    """解析完整的全身图模板。双足姿态随生成链路路由（``a_pose``）：2D 立绘走自然站姿——
    see-through 拆分不要求 A-pose；3D 种子（``a_pose=True``）保持 A-pose 供绑骨识别与多视角一致性，画风不影响姿态。"""
    if species in _SPECIES_TEMPLATES:
        template = _SPECIES_TEMPLATES[species]
    else:
        flavor = _SPECIES_FLAVOR.get(species, "")
        template = _RIG_TYPE_TEMPLATES.get(rig_type, _RIG_TYPE_TEMPLATES["biped"])
        if flavor:
            template = replace(template, flavor=flavor)
    if template.rig_type == "biped":
        template = replace(template, pose=BIPED_A_POSE if a_pose else BIPED_NATURAL_POSE)
    return template if template.style == style else replace(template, style=style)


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
    identity_anchor: IdentityAnchor = "reference",
    canvas_aspect: str | None = None,
) -> str:
    """为某个视角拼装一条生图 prompt（无 LLM 往返）；由 ``application/generation/avatar_service`` 按视角调用。全身图由外貌设定、性格特点、画风词典与用户额外要求装配，外形特征由主参考图锚定（正面种子源自全身种子图、背面种子源自正面种子、换装主参考为全身种子图），不带入头像阶段特异性的 avatar_prompt。identity_anchor 语义见 prompts.generation.IdentityAnchor。canvas_aspect 写入画幅宽高比（如 "9:16"），供没有独立 size 通道的自备图语境告知比例；AI 路径的画幅由生图请求的 size 传达，不传。"""
    style_key = style_id or template.style or "refined_anime_cg"
    style_wording = FULLBODY_STYLE_WORDING.get(style_key, FULLBODY_STYLE_WORDING["refined_anime_cg"])
    features = getattr(template, f"{view}_features", "")

    if persona is not None:
        definition = persona if isinstance(persona, dict) else _persona_payload(persona)
        if not appearance:
            appearance = str(definition.get("appearance") or "").strip()
        if not personality:
            personality = str(definition.get("personality") or "").strip()

    frame_clause = (
        "从头到脚完整可见的全身构图，头顶与双脚（含鞋履）完整入画且四周留有安全边距，"
        "不裁切头顶、脚部、肢体、翅膀或尾部，不得改成半身或膝上构图；平视镜头，透视自然。"
    )
    if canvas_aspect:
        frame_clause = f"画幅比例 {canvas_aspect}；{frame_clause}"
    view_label = VIEW_PREFIX.get(view, "正面全身角色立绘")
    if identity_anchor == "reference-self-source":
        # 自备图：提示词以改写句式开头，同时适配外部工具图生图与纯文生图。
        head = SELF_SOURCE_REWRITE_LEAD.format(target=view_label)
        identity_clause = SELF_SOURCE_REFERENCE_CLAUSE
    else:
        head = f"{view_label}，单一角色居中。"
        identity_clause = "以参考图为身份锚点，保持同一角色的脸、体型、物种与标志性特征。"
    constraint_scope = "指定视角、姿势、身份锚点或背景规则"
    parts = [
        head,
        f"{template.pose}{features}",
        frame_clause,
        identity_clause,
        style_wording,
    ]

    identity_parts: list[str] = []
    appearance_clause = _prompt_clause(appearance)
    personality_clause = _prompt_clause(personality)
    if appearance_clause:
        identity_parts.append(f"外形特征：{appearance_clause}")
    if personality_clause:
        identity_parts.append(f"性格气质：{personality_clause}（只通过克制的神态和姿态体现）")

    if identity_parts:
        parts.append(f"角色设定：{'；'.join(identity_parts)}。")

    if template.flavor:
        parts.append(template.flavor)
    feedback_clause = _prompt_clause(feedback or "")
    if feedback_clause:
        parts.append(f"用户补充的视觉要求：{feedback_clause}。仅在不冲突时采用，不得覆盖{constraint_scope}。")
    parts.append("纯白无缝平面背景，均匀柔和的棚拍光；无场景、地面投影、道具、边框、文字、标志或水印。")
    return "".join(parts)


def build_outfit_prompt(
    *,
    template: FullbodyTemplate,
    style_id: str | None = None,
    feedback: str,
    appearance: str = "",
    personality: str = "",
    identity_anchor: IdentityAnchor = "reference",
    canvas_aspect: str | None = None,
) -> str:
    """换装立绘 prompt：在正面全身 prompt 之上叠加「锁身份、换穿着」约束；身份与身材由主参考图（独立全身种子图）锚定，着装要求进 feedback 槽。identity_anchor 语义同 build_fullbody_prompt。canvas_aspect 语义同 build_fullbody_prompt。"""
    base = build_fullbody_prompt(
        "front",
        template=template,
        style_id=style_id,
        appearance=appearance,
        personality=personality,
        identity_anchor=identity_anchor,
        canvas_aspect=canvas_aspect,
    )
    tail = "不得因此覆盖从头到脚的全身构图、标准姿势、身份锚点或纯白背景。"
    return f"{base}{OUTFIT_CHANGE_CLAUSE}着装要求：{_prompt_clause(feedback)}。鞋履与脚部同样完整入画。{tail}"


async def describe_garment_image(
    user_id: int | None,
    image_uri: str,
    requirement: str = "",
) -> str:
    """把用户服装参考图（可附文字要求）整合为一段着装设计稿，再进着装要求；生图调用恒为单参考图（身份锚点），
    消除双图条件下的画风与身份渗漏。

    requirement 是用户随图附带的着装要求，非空时与图片一起交给模型整合，冲突处以用户要求为准。
    视觉链为空时抛 MissingLlmConfigError，全链失败抛 RuntimeError；由调用方决定降级与文案。
    链解析用独立短会话，视觉调用期间不占调用方连接（短会话纪律）。"""
    async with SESSION_LOCAL() as chain_db:
        chain = await resolve_vision_chain(chain_db, user_id)
    if not chain:
        raise MissingLlmConfigError("no vision-capable llm provider configured")
    content: list[dict[str, str]] = [{"type": "input_image", "image_url": image_uri}]
    user_requirement = requirement.strip()
    if user_requirement:
        content.append({"type": "input_text", "text": f"用户对着装的要求：{user_requirement}"})
    errors: list[str] = []
    for config in chain:
        try:
            client = provider_from_config(config).raw_client()
            if client is None:
                errors.append(f"{config.provider_name}: no responses client")
                continue
            response = await call_with_retry(
                client,
                **build_responses_kwargs(
                    model=config.model,
                    instructions=GARMENT_DESCRIBE_SYSTEM,
                    input_items=[{"role": "user", "content": content}],
                    max_output_tokens=1000,
                ),
            )
            description = response.output_text.strip()
            if description:
                return description
            errors.append(f"{config.provider_name}: empty description")
        except Exception as exc:
            errors.append(f"{config.provider_name}: {exc}")
    raise RuntimeError(f"garment image describe failed: {'; '.join(errors)}")
