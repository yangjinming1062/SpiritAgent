"""生活空间场景提示词；全身参考只约束固定身份。"""

from prompts.generation import (
    SCENE_IMAGE_RULES,
    SCENE_NOTES_TEMPLATE,
    SCENE_OUTFIT_TEMPLATE,
    SCENE_REFERENCE,
    SCENE_TEMPLATE,
)


def build_scene_prompt(*, notes: str, has_reference_image: bool = False, outfit_description: str = "") -> str:
    parts = [SCENE_TEMPLATE.format(identity_reference="图 1" if has_reference_image else "参考图")]
    if notes.strip():
        parts.append(SCENE_NOTES_TEMPLATE.format(notes=notes.strip()))
    if outfit_description.strip():
        parts.append(SCENE_OUTFIT_TEMPLATE.format(outfit=outfit_description.strip()))
    if has_reference_image:
        parts.append(SCENE_REFERENCE)
    parts.append(SCENE_IMAGE_RULES)
    return "\n".join(parts)
