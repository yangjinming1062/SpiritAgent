"""角色提示词装配与视觉模型辅助；身体结构不经过固定物种分类。"""

import json
from typing import Any

from components import LLM_MAX_OUTPUT_TOKENS, SESSION_LOCAL, get_logger, safe_json_loads
from modules.companion import Persona
from prompts.generation import (
    AVATAR_SYSTEM_PROMPT,
    CHARACTER_FORM_INSTRUCTIONS,
    CHARACTER_FORM_KEEP_BODY,
    CHARACTER_FORM_REDRAW_BODY,
    CHARACTER_VISUAL_STYLE,
    FULLBODY_FRAME,
    FULLBODY_PRESERVE_CHARACTER,
    FULLBODY_REWRITE_LEAD,
    GARMENT_DESCRIBE_SYSTEM,
    IMAGE_EDIT_TEMPLATE,
    OUTFIT_CHANGE_TEMPLATE,
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
from .user_config import UserLlmConfig

logger = get_logger(__name__)


class VisualReasoningError(RuntimeError):
    """视觉推理失败；异常文本可展示，供应商诊断只写入日志。"""


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
    if response.status != "completed":
        raise RuntimeError(f"Prompt response not completed: {response.status}")
    text = response.output_text.strip()
    if not text:
        raise RuntimeError("prompt enhancer returned an empty response")
    return text


async def call_llm_once(
    llm_cfg: UserLlmConfig,
    system_prompt: str,
    user_payload: Any,
    *,
    max_output_tokens: int,
    reasoning_effort: str | None = None,
    json_output: bool = False,
) -> str:
    """执行单次非流式调用；推理档位仅在当前供应商明确支持时下发。"""
    client = client_for_config(llm_cfg)
    provider_name = llm_cfg.provider_name
    context_length = resolve_context_tokens(provider_name, ServiceType.llm)
    provider_cls = try_resolve(ServiceType.llm, provider_name)
    supported_efforts = getattr(provider_cls, "REASONING_EFFORTS", frozenset())
    reasoning = {"effort": reasoning_effort} if reasoning_effort in supported_efforts else None
    user_content = (
        json.dumps(user_payload, ensure_ascii=False) if isinstance(user_payload, dict | list) else str(user_payload)
    )
    request = build_responses_kwargs(
        model=llm_cfg.model_name,
        instructions=system_prompt,
        input_items=[{"role": "user", "content": [{"type": "input_text", "text": user_content}]}],
        max_output_tokens=max_output_tokens,
        reasoning=reasoning,
        text={"format": {"type": "json_object"}}
        if json_output and getattr(provider_cls, "supports_json_object", False)
        else None,
    )
    resp = await call_with_retry(client, context_length=context_length, **request)
    if resp is None or resp.status != "completed":
        raise RuntimeError(f"LLM response not completed: {getattr(resp, 'status', None)}")
    return resp.output_text


async def enhance_avatar_prompt(
    db: AsyncSession | None,
    user_id: int | None,
    persona: Persona,
    *,
    feedback: str | None = None,
    has_reference: bool = False,
    provider_config: ProviderConfig | None = None,
) -> str:
    """把 persona 定义改写为中文头像 prompt 并附上统一风格；图像参考另在生图时传入。"""
    visual = _persona_visual_payload(persona, feedback)
    if has_reference:
        visual = {key: visual[key] for key in ("personality", "feedback")}
    payload = {**visual, "has_reference": has_reference}
    user_payload = json.dumps(payload, ensure_ascii=False)
    raw = await chat(db, user_id, AVATAR_SYSTEM_PROMPT, user_payload, provider_config=provider_config)
    return _strip_markdown_fence(raw) + "\n\n" + CHARACTER_VISUAL_STYLE


async def describe_character_form(
    user_id: int | None,
    *,
    species: str,
    appearance: str,
    personality: str,
    feedback: str = "",
    outfit_description: str = "",
    previous_feedback: list[str] | None = None,
    body_baseline: dict[str, str] | None = None,
    reference_images: tuple[str, ...],
    identity: str = "",
    allow_body_change: bool = False,
) -> str:
    """视觉模型根据开放描述与实际参考判断材质、结构和稳定待机姿态。"""
    return await vision_chat(
        user_id,
        CHARACTER_FORM_INSTRUCTIONS
        + "\n\n"
        + (CHARACTER_FORM_REDRAW_BODY if allow_body_change else CHARACTER_FORM_KEEP_BODY)
        + ("\n\n" + identity if identity else ""),
        json.dumps(
            {
                "biological_type": species,
                "appearance": appearance,
                "personality": personality,
                "feedback": feedback,
                "outfit_description": outfit_description,
                "previous_feedback": previous_feedback or [],
                "body_baseline": body_baseline or {},
            },
            ensure_ascii=False,
        ),
        reference_images=reference_images,
    )


async def build_outfit_prompt(
    *,
    user_id: int,
    reference_image: str,
    species: str,
    requirement: str,
    identity: str,
    feedback: str = "",
    previous_feedback: list[str] | None = None,
    personality: str = "",
    canvas_aspect: str | None = None,
) -> str:
    """换装与自备图共用动态身体判断；固定统一视觉风格与身份、画幅边界。"""
    direction = await describe_character_form(
        user_id,
        species=species,
        appearance="",
        identity=identity,
        personality=personality,
        feedback=feedback,
        previous_feedback=previous_feedback,
        outfit_description=requirement,
        reference_images=(reference_image,),
    )
    return "\n".join(
        (
            FULLBODY_REWRITE_LEAD,
            FULLBODY_PRESERVE_CHARACTER,
            identity,
            CHARACTER_VISUAL_STYLE,
            f"画幅比例 {canvas_aspect}；{FULLBODY_FRAME}" if canvas_aspect else FULLBODY_FRAME,
            direction,
            OUTFIT_CHANGE_TEMPLATE.format(
                requirements=json.dumps(
                    {
                        "outfit_description": requirement,
                        "previous_feedback": previous_feedback or [],
                        "feedback": feedback,
                    },
                    ensure_ascii=False,
                ),
            ),
            "纯白无缝平面背景，均匀柔和棚拍光；稳定待机姿态，无场景、投影、道具、文字或水印。",
        ),
    )


async def vision_chat(
    user_id: int | None,
    system_prompt: str,
    user_payload: str,
    *,
    reference_images: tuple[str, ...],
) -> str:
    """带实际图像的视觉推理；独立短会话解析配置，无可用模型时明确失败。"""
    if not reference_images or any(not uri for uri in reference_images):
        raise ValueError("visual reasoning requires readable reference images")
    async with SESSION_LOCAL() as chain_db:
        chain = await resolve_vision_chain(chain_db, user_id)
    if not chain:
        raise VisualReasoningError("未配置视觉模型，请先配置支持图片的模型")
    content = [{"type": "input_image", "image_url": uri} for uri in reference_images]
    content.append({"type": "input_text", "text": user_payload})
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
                    instructions=system_prompt,
                    input_items=[{"role": "user", "content": content}],
                    max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
                ),
            )
            if response.status != "completed":
                errors.append(f"{config.provider_name}: incomplete response ({response.status})")
                continue
            result = _strip_markdown_fence(response.output_text)
            if result:
                return result
            errors.append(f"{config.provider_name}: empty response")
        except Exception as exc:
            errors.append(f"{config.provider_name}: {exc}")
    logger.warning("visual reasoning failed", extra={"user_id": user_id, "errors": errors})
    raise VisualReasoningError("视觉分析失败，请稍后重试")


async def describe_garment_image(
    user_id: int | None,
    image_uri: str,
    requirement: str = "",
) -> str:
    """将服装参考图与文字要求整合为可迁移的着装设计稿。"""
    return await vision_chat(
        user_id,
        GARMENT_DESCRIBE_SYSTEM,
        json.dumps({"requirement": requirement.strip()}, ensure_ascii=False),
        reference_images=(image_uri,),
    )
