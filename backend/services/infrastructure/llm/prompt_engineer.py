"""图生提示词组装引擎 — 确定性提示词构建器与LLM辅助工具。

数据流转：Persona 表（definition_json）→ 物种模板与姿态路由（resolve_fullbody_template）
与角色身份设定提取（appearance / personality）→ build_fullbody_prompt() 拼接
[画风描述] + [角色设定] + [物种特效] + [反馈] → image_generation_tool 发送至生图供应商
（MiniMax / Gemini / Grok）生成种子图。

对外公开的提示词构建方法：
- enhance_avatar_prompt()   [LLM]      Persona 角色定义 → 半身头像图（bust avatar）提示词
- describe_garment_image()  [LLM]      用户服装参考图与文字要求 → 整合的着装设计稿（换装参考不直传生图，生图恒单参考）
- build_fullbody_prompt()   [确定性]   物种姿态模板 + 画风 + Persona 设定 → 全身立绘提示词

全身图提示词按稳定优先级组装：主体 → 物种姿态 → 完整画幅 →
参考图身份锚点 → 渲染风格 → Persona 外观与克制气质 → 物种特效 → 不冲突的用户反馈 →
纯白背景与排除项。

辅助工具说明：物种姿态路由、体态模板定义见下文各常量与类。
"""

import json
from dataclasses import dataclass, replace
from typing import Any, Final, Literal

from components import SESSION_LOCAL, safe_json_loads
from modules.companion import Persona
from prompts.generation import (
    AVATAR_SYSTEM_PROMPT,
    BIPED_A_POSE,
    BIPED_NATURAL_POSE,
    FULLBODY_FRONT_LABEL,
    FULLBODY_PRESERVE_CHARACTER,
    FULLBODY_REWRITE_LEAD,
    FULLBODY_STYLE_WORDING,
    GARMENT_DESCRIBE_SYSTEM,
    IMAGE_EDIT_TEMPLATE,
    OUTFIT_CHANGE_TEMPLATE,
    UNCHOPPED_BODY_PARTS,
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

FullbodyStyle = Literal["refined_anime_cg", "anime_illustration"]

# 外观参考链（外观参考立绘、外观及对应自备图提示词）的服务端固定画风。该链交付的是
# 动漫插画风格的角色参考图，写实或照片级输入不适合后续形象链消费，因此画风服务端固定，
# 也不消费客户端传参。
REFERENCE_ILLUSTRATION_STYLE: Final = "anime_illustration"


@dataclass(frozen=True)
class FullbodyTemplate:
    features: str
    pose: str
    flavor: str = ""
    style: FullbodyStyle = "refined_anime_cg"


def _biped_template(pose: str) -> FullbodyTemplate:
    return FullbodyTemplate(
        features="身体朝向正前方，正面视点。",
        pose=pose,
    )


_SPECIES_TEMPLATES: dict[str, FullbodyTemplate] = {
    "人类": _biped_template(BIPED_NATURAL_POSE),
    "精灵": _biped_template(BIPED_NATURAL_POSE),
    "机甲": _biped_template(BIPED_A_POSE),
}

_SPECIES_FLAVOR: dict[str, str] = {
    "灵兽": "原图若有发光纹路、标记或图腾，保留其形状与分布，不额外添加。",
    "幻形": "原图若有半透明、发光或粒子效果，保留其表现，不改变角色轮廓。",
}


def build_image_edit_prompt(feedback: str, *, preserve: str) -> str:
    """图像编辑 prompt：输入图是编辑底图（上一版产物），只按用户本次反馈做增量修改。"""
    clause = _prompt_clause(feedback)
    if not clause:
        raise ValueError("image edit requires non-empty feedback")
    return IMAGE_EDIT_TEMPLATE.format(feedback=clause, preserve=preserve)


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


def resolve_fullbody_template(
    species: str,
    style: FullbodyStyle = "refined_anime_cg",
) -> FullbodyTemplate:
    """解析完整的全身图模板：人类与精灵用自然站姿，机甲用 A-pose，其余物种回退自然站姿并附加物种特效；
    画风不影响姿态。"""
    if species in _SPECIES_TEMPLATES:
        template = _SPECIES_TEMPLATES[species]
    else:
        template = _biped_template(BIPED_NATURAL_POSE)
        flavor = _SPECIES_FLAVOR.get(species, "")
        if flavor:
            template = replace(template, flavor=flavor)
    return template if template.style == style else replace(template, style=style)


def _fullbody_frame_clause(canvas_aspect: str | None) -> str:
    """完整画幅约束：统一描述，不按物种枚举可能不存在的部位（翅膀/尾巴等）。"""
    clause = (
        "从头到脚完整可见的全身构图，主体完整入画且四周留有安全边距，"
        f"不裁切{UNCHOPPED_BODY_PARTS}，不得改成半身或膝上构图；镜头平视，透视自然。"
    )
    if canvas_aspect:
        return f"画幅比例 {canvas_aspect}；{clause}"
    return clause


def build_fullbody_prompt(
    *,
    template: FullbodyTemplate,
    style_id: FullbodyStyle | None = None,
    feedback: str | None = None,
    appearance: str = "",
    personality: str = "",
    avatar_prompt: str = "",
    persona: Persona | dict | None = None,
    canvas_aspect: str | None = None,
) -> str:
    """拼装一条正面全身生图 prompt（无 LLM 往返）；由 ``application/generation/avatar_service`` 与换装链调用。全身图由外貌设定、性格特点、画风词典与用户额外要求装配，外形特征由主参考图锚定（外观参考源自全身种子图、换装主参考为全身种子图），不带入头像阶段特异性的 avatar_prompt。canvas_aspect 写入画幅宽高比（如 "9:16"），供没有独立 size 通道的自备图语境告知比例；AI 路径的画幅由生图请求的 size 传达，不传。"""
    style_key = style_id or template.style or "refined_anime_cg"
    style_wording = FULLBODY_STYLE_WORDING.get(style_key, FULLBODY_STYLE_WORDING["refined_anime_cg"])

    if persona is not None:
        definition = persona if isinstance(persona, dict) else _persona_payload(persona)
        if not appearance:
            appearance = str(definition.get("appearance") or "").strip()
        if not personality:
            personality = str(definition.get("personality") or "").strip()

    frame_clause = _fullbody_frame_clause(canvas_aspect)
    head = FULLBODY_REWRITE_LEAD.format(target=FULLBODY_FRONT_LABEL)
    identity_clause = FULLBODY_PRESERVE_CHARACTER
    constraint_scope = "角色身份、指定视角、姿势、画风或背景规则"
    parts = [
        head,
        f"{template.pose}{template.features}",
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
    style_id: FullbodyStyle | None = None,
    feedback: str,
    appearance: str = "",
    personality: str = "",
    canvas_aspect: str | None = None,
) -> str:
    """换装立绘 prompt：在正面全身 prompt 之上叠加「锁身份、换穿着」约束；身份与身材由主参考图（独立全身种子图）锚定，着装要求进 feedback 槽。canvas_aspect 语义同 build_fullbody_prompt。"""
    base = build_fullbody_prompt(
        template=template,
        style_id=style_id,
        appearance=appearance,
        personality=personality,
        canvas_aspect=canvas_aspect,
    )
    return base + OUTFIT_CHANGE_TEMPLATE.format(requirement=_prompt_clause(feedback))


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
