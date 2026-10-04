"""环境壁纸提示词；伙伴本人由独立桌面形象呈现。"""

from prompts.generation import SCENE_IMAGE_RULES, SCENE_NOTES_TEMPLATE, SCENE_REFERENCE, SCENE_TEMPLATE


def build_scene_prompt(*, notes: str, aspect_ratio: str, has_reference_image: bool = False) -> str:
    parts = [SCENE_TEMPLATE.format(aspect_ratio=aspect_ratio)]
    if notes.strip():
        parts.append(SCENE_NOTES_TEMPLATE.format(notes=notes.strip()))
    if has_reference_image:
        parts.append(SCENE_REFERENCE)
    parts.append(SCENE_IMAGE_RULES)
    return "\n".join(parts)
