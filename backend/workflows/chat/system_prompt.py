import json
import re

from features.appearance.character_card import load_character_snapshot
from features.companion.prompts import OUTFIT_DESCRIPTION_LABELS_TEXTS, OUTFIT_LABELS_TEXTS
from features.media.identity_rendering import render_character_appearance
from infrastructure.text import resolve_prompt_text
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.chat.prompt_blocks import AgentPromptConfig, render_preset_body, volatile_header_value
from workflows.chat.prompt_presets import preset_body
from workflows.chat.prompts import (
    DESKTOP_ENVIRONMENT_GUIDANCES,
    OUTFIT_DEMEANOR_GUIDANCES,
    SCENE_CONTEXT_GUIDANCES,
    VOLATILE_LABELS,
)
from workflows.companion_actions import ActionViewSnapshot

# volatile header 行：发送前按同一格式整行刷新日期与时区，保留构建时按会话语言写入的标签。
_VOLATILE_HEADER_RE = re.compile(
    "(?m)^(?P<label>" + "|".join(re.escape(label) for label in VOLATILE_LABELS.values()) + ")(?P<date>.*)$",
)


def build_system_prompt(config: AgentPromptConfig, *, preset_id: str, delegated: bool = False) -> str:
    return render_preset_body(preset_body(preset_id, config.language, delegated=delegated), config)


async def build_companion_environment_prompt(
    db: AsyncSession,
    user_id: int,
    *,
    language: str,
    action_view: ActionViewSnapshot,
) -> str:
    """呈现资料统一读实际呈现快照：窗口读静态造型与环境资料，桌面读成品画面描述。"""

    parts: list[str] = []
    presentation = action_view.presentation
    if action_view.presentation_mode == "desktop":
        current = {
            "visual_description": presentation.visual_description,
            "outfit_description": presentation.outfit_description,
        }
        parts.extend(
            [
                resolve_prompt_text(DESKTOP_ENVIRONMENT_GUIDANCES, language),
                json.dumps({"current": current}, ensure_ascii=False),
            ],
        )
        if presentation.outfit_description:
            parts.append(resolve_prompt_text(OUTFIT_DEMEANOR_GUIDANCES, language))
        parts.append(action_view.to_prompt_block(language=language))
        return "\n\n".join(parts)
    if appearance := render_character_appearance(await load_character_snapshot(db, user_id), language=language):
        parts.append(appearance)
    if presentation.outfit_description:
        parts.append(resolve_prompt_text(OUTFIT_LABELS_TEXTS, language))
        parts.append(
            f"{resolve_prompt_text(OUTFIT_DESCRIPTION_LABELS_TEXTS, language)}: {presentation.outfit_description}",
        )
        parts.append(resolve_prompt_text(OUTFIT_DEMEANOR_GUIDANCES, language))
    parts.extend(
        [
            resolve_prompt_text(SCENE_CONTEXT_GUIDANCES, language),
            json.dumps({"scene_description": presentation.scene_description}, ensure_ascii=False),
        ],
    )
    # 动作快照：每次模型调用前刷新（含工具续轮），频繁变化的动作数据不写入稳定身份前缀。
    parts.append(action_view.to_prompt_block(language=language))
    return "\n\n".join(parts)


def refresh_volatile_header_in_prompt(instructions: str, *, user_local_tz: str | None, lang: str) -> str:
    """发送前刷新 volatile header 行的日期，使长工具循环跨日后仍是当天日期；其余块由每回合重建覆盖。"""
    match = _VOLATILE_HEADER_RE.search(instructions)
    if match is None:
        return instructions
    line = f"{match.group('label')}{volatile_header_value(user_local_tz, lang)}"
    return _VOLATILE_HEADER_RE.sub(lambda _: line, instructions, count=1)
