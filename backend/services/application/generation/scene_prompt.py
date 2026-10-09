"""窗口生活空间场景提示词；伙伴本人由窗口精灵呈现。"""

from prompts.generation import SCENE_IMAGE_RULES, SCENE_NOTES_TEMPLATE, SCENE_REFERENCE, SCENE_TEMPLATE


def build_scene_prompt(*, notes: str, has_reference_image: bool = False) -> str:
    parts = [SCENE_TEMPLATE]
    if notes.strip():
        parts.append(SCENE_NOTES_TEMPLATE.format(notes=notes.strip()))
    if has_reference_image:
        parts.append(SCENE_REFERENCE)
    parts.append(SCENE_IMAGE_RULES)
    return "\n".join(parts)
