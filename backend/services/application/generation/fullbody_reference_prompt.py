"""独立全身肖像的参考分工与创作资料装配。"""

import json

from prompts.generation import CHARACTER_VISUAL_STYLE, FULLBODY_REFERENCE_TEMPLATE, FULLBODY_SECONDARY_REFERENCE


def build_fullbody_reference_prompt(
    *,
    body_direction: str,
    species: str,
    gender: str,
    appearance: str,
    personality: str,
    feedback: str | None,
    has_user_reference: bool,
    body_baseline: dict[str, str] | None = None,
    outfit_description: str = "",
    canvas_aspect: str | None = None,
) -> str:
    """AI 与自备图共用头像扩展语义；自备图通过 canvas_aspect 告知画幅，AI 由请求 size 传达。"""
    return FULLBODY_REFERENCE_TEMPLATE.format(
        style=CHARACTER_VISUAL_STYLE,
        body_direction=body_direction,
        portrait_reference="参考图 1" if has_user_reference else "参考图",
        secondary_reference=FULLBODY_SECONDARY_REFERENCE if has_user_reference else "",
        aspect=f"画幅比例 {canvas_aspect}；" if canvas_aspect else "",
        payload=json.dumps(
            {
                "species": species,
                "gender": gender,
                "appearance": appearance,
                "personality": personality,
                "feedback": feedback or "",
                "body_baseline": body_baseline or {},
                "outfit_description": outfit_description,
            },
            ensure_ascii=False,
        ),
    )
