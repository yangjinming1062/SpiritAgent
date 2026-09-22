"""生活空间场景提示词；全身参考只约束固定身份。"""

from dataclasses import dataclass

from prompts.generation import (
    SCENE_IMAGE_RULES,
    SCENE_NOTES_TEMPLATE,
    SCENE_OUTFIT_TEMPLATE,
    SCENE_REFERENCE,
    SCENE_TEMPLATE,
)


@dataclass(frozen=True)
class ScenePromptContext:
    notes: str = ""
    has_reference_image: bool = False
    outfit_description: str = ""


def build_scene_prompt(ctx: ScenePromptContext) -> str:
    parts = [SCENE_TEMPLATE.format(identity_reference="图 1" if ctx.has_reference_image else "参考图")]
    if ctx.notes.strip():
        parts.append(SCENE_NOTES_TEMPLATE.format(notes=ctx.notes.strip()))
    if ctx.outfit_description.strip():
        parts.append(SCENE_OUTFIT_TEMPLATE.format(outfit=ctx.outfit_description.strip()))
    if ctx.has_reference_image:
        parts.append(SCENE_REFERENCE)
    parts.append(SCENE_IMAGE_RULES)
    return "\n".join(parts)
