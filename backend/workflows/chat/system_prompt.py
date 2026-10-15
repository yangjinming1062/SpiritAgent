import json
import re

from features.actions.context import build_action_context
from features.appearance.context import build_outfit_extras
from features.companion.proactive_runtime import get_presentation_snapshot
from features.desktop_life.queries import activated_desktop_environment
from features.desktop_life.service import build_desktop_action_context
from features.scenes.queries import get_scene_state, scene_environment
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

# volatile header 行：发送前按同一格式整行刷新日期与时区，保留构建时按会话语言写入的标签。
_VOLATILE_HEADER_RE = re.compile(
    "(?m)^(?P<label>" + "|".join(re.escape(label) for label in VOLATILE_LABELS.values()) + ")(?P<date>.*)$",
)


def build_system_prompt(config: AgentPromptConfig, *, preset_id: str, delegated: bool = False) -> str:
    return render_preset_body(preset_body(preset_id, config.language, delegated=delegated), config)


async def build_companion_environment_prompt(db: AsyncSession, user_id: int, *, language: str) -> str:
    parts: list[str] = []
    presentation = get_presentation_snapshot(user_id)
    if presentation is not None and presentation.mode == "desktop":
        parts.extend(
            [
                resolve_prompt_text(DESKTOP_ENVIRONMENT_GUIDANCES, language),
                json.dumps(await activated_desktop_environment(db, user_id), ensure_ascii=False),
                await build_desktop_action_context(db, user_id, language=language),
            ],
        )
        return "\n\n".join(parts)
    state = await get_scene_state(db, user_id)
    if outfit := await build_outfit_extras(db, user_id, language=language):
        parts.append(outfit)
        parts.append(resolve_prompt_text(OUTFIT_DEMEANOR_GUIDANCES, language))
    parts.extend(
        [
            resolve_prompt_text(SCENE_CONTEXT_GUIDANCES, language),
            json.dumps(scene_environment(state), ensure_ascii=False),
        ],
    )
    # 动作快照：每次模型调用前刷新（含工具续轮），频繁变化的动作数据不写入稳定身份前缀。
    action_context = await build_action_context(db, user_id)
    parts.append(action_context.to_prompt_block(language=language))
    return "\n\n".join(parts)


def refresh_volatile_header_in_prompt(instructions: str, *, user_local_tz: str | None, lang: str) -> str:
    """发送前刷新 volatile header 行的日期，使长工具循环跨日后仍是当天日期；其余块由每回合重建覆盖。"""
    match = _VOLATILE_HEADER_RE.search(instructions)
    if match is None:
        return instructions
    line = f"{match.group('label')}{volatile_header_value(user_local_tz, lang)}"
    return _VOLATILE_HEADER_RE.sub(lambda _: line, instructions, count=1)
