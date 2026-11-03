"""可选视觉观察的纯文本呈现，不读取任何外观运行状态。"""

import json

from infrastructure.text import resolve_prompt_text

from features.companion.prompts import CHARACTER_APPEARANCE_TEXTS

from .identity_contracts import VisualObservation


def render_character_appearance(snapshot: VisualObservation | None, *, language: str) -> str:
    """无参考图的文字任务读取的观察资料；图像任务直接使用确认图。"""
    if snapshot is None:
        return ""
    return resolve_prompt_text(CHARACTER_APPEARANCE_TEXTS, language).format(
        features=json.dumps(snapshot.model_dump(), ensure_ascii=False),
    )
