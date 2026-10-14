"""冻结身份资料的纯文本呈现，不读取任何外观运行状态。"""

import json

from infrastructure.text import resolve_prompt_text

from features.companion.prompts import CHARACTER_APPEARANCE_TEXTS

from .identity_contracts import CharacterCardSnapshot


def render_character_appearance(snapshot: CharacterCardSnapshot | None, *, language: str) -> str:
    """无参考图的文字任务读取的外形资料；图像任务使用身份参考资料。"""
    if snapshot is None:
        return ""
    return resolve_prompt_text(CHARACTER_APPEARANCE_TEXTS, language).format(
        features=json.dumps(snapshot.features.model_dump(), ensure_ascii=False),
    )
