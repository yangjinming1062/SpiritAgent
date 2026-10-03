"""头像与衣柜视觉提示词装配；调用与传输通过 LLM 公共入口。"""

import json

from components import strip_outer_code_fence
from modules.companion import Persona, parse_persona_definition
from prompts.generation import (
    AVATAR_IMAGE_RULES,
    AVATAR_PRESENTATION_REFERENCE,
    AVATAR_REFERENCE_TEMPLATE,
    AVATAR_SYSTEM_PROMPT,
    CHARACTER_FORM_INSTRUCTIONS,
    CHARACTER_FORM_KEEP_BODY,
    CHARACTER_FORM_REDRAW_BODY,
    CHARACTER_FORM_SECONDARY_REFERENCE,
    CHARACTER_VISUAL_STYLE,
    DEFAULT_VISUAL_STYLE,
    FULLBODY_FRAME,
    FULLBODY_PRESERVE_CHARACTER,
    GARMENT_DESCRIBE_SYSTEM,
    IMAGE_EDIT_TEMPLATE,
    OUTFIT_BACKGROUND,
    OUTFIT_BODY_DIRECTION_TEMPLATE,
    OUTFIT_CHANGE_LEAD,
    OUTFIT_CHANGE_TEMPLATE,
)

from services.infrastructure.llm import chat, vision_chat


def build_image_edit_prompt(feedback: str, *, preserve: str) -> str:
    """图像编辑 prompt：输入图是编辑底图（上一版产物），只按用户本次反馈做增量修改。"""
    clause = _prompt_clause(feedback)
    if not clause:
        raise ValueError("image edit requires non-empty feedback")
    return IMAGE_EDIT_TEMPLATE.format(feedback=clause, preserve=preserve)


def _prompt_clause(value: str) -> str:
    """把外部描述收成可嵌入句子的片段，避免装配后出现重复句号。"""
    return value.strip().rstrip("。.!！?？;； ")


def _persona_visual_payload(persona: Persona, feedback: str | None) -> dict[str, str]:
    definition = parse_persona_definition(persona.definition_json)
    return {
        "biological_type": definition.get("biological_type") or "",
        "gender": definition.get("gender") or "",
        "personality": definition.get("personality") or "",
        "feedback": (feedback or "").strip(),
    }


def build_avatar_reference_prompt(
    *,
    personality: str,
    feedback: str | None = None,
    has_presentation_reference: bool = False,
    description: str = "",
) -> str:
    """装配身份参考头像提示词；明确外貌文字只覆盖其涉及的视觉维度。"""
    prompt = AVATAR_REFERENCE_TEMPLATE.format(
        reference="图 1" if has_presentation_reference else "参考图",
        presentation=AVATAR_PRESENTATION_REFERENCE if has_presentation_reference else "",
        payload=json.dumps(
            {
                "personality": personality.strip(),
                "feedback": (feedback or "").strip(),
            },
            ensure_ascii=False,
        ),
        description=f"画面与神态建议：{description.strip()}" if description.strip() else "",
    )
    return "\n\n".join((prompt.strip(), AVATAR_IMAGE_RULES, CHARACTER_VISUAL_STYLE))


async def enhance_avatar_prompt(
    user_id: int | None,
    persona: Persona,
    *,
    feedback: str | None = None,
    has_reference: bool = False,
) -> str:
    """整理角色资料；无图时返回完整提示词，有图时返回供参考图装配器使用的画面建议。"""
    visual = _persona_visual_payload(persona, feedback)
    if has_reference:
        visual = {key: visual[key] for key in ("personality", "feedback")}
    payload = {**visual, "has_reference": has_reference}
    user_payload = json.dumps(payload, ensure_ascii=False)
    raw = await chat(user_id, AVATAR_SYSTEM_PROMPT, user_payload)
    description = strip_outer_code_fence(raw)
    if has_reference:
        return description
    return "\n\n".join((description, AVATAR_IMAGE_RULES, DEFAULT_VISUAL_STYLE))


async def describe_character_form(
    user_id: int | None,
    *,
    species: str,
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
        + ("\n\n" + CHARACTER_FORM_SECONDARY_REFERENCE if len(reference_images) > 1 else "")
        + ("\n\n" + identity if identity else ""),
        json.dumps(
            {
                "biological_type": species,
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
        identity=identity,
        personality=personality,
        feedback=feedback,
        previous_feedback=previous_feedback,
        outfit_description=requirement,
        reference_images=(reference_image,),
    )
    return "\n".join(
        (
            OUTFIT_CHANGE_LEAD,
            FULLBODY_PRESERVE_CHARACTER,
            identity,
            CHARACTER_VISUAL_STYLE,
            f"画幅比例 {canvas_aspect}；{FULLBODY_FRAME}" if canvas_aspect else FULLBODY_FRAME,
            OUTFIT_BODY_DIRECTION_TEMPLATE.format(direction=direction),
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
            OUTFIT_BACKGROUND,
        ),
    )


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
