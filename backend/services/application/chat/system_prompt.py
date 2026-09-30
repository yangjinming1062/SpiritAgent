import json
import re

from components import resolve_language, resolve_prompt_text
from prompts.chat import OUTFIT_DEMEANOR_GUIDANCES, OUTFIT_SOURCE_TEXTS, SCENE_CONTEXT_GUIDANCES, VOLATILE_LABELS
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.actions.context import build_action_context
from services.domains.companion import build_outfit_extras, get_scene_state, scene_environment

from .prompt_blocks import AgentPromptConfig, render_preset_body, volatile_header_value
from .prompt_presets import preset_body

# volatile header 行：发送前按同一格式整行刷新日期与时区，保留构建时按会话语言写入的标签。
_VOLATILE_HEADER_RE = re.compile(
    "(?m)^(?P<label>" + "|".join(re.escape(label) for label in VOLATILE_LABELS.values()) + ")(?P<date>.*)$",
)


def build_system_prompt(config: AgentPromptConfig, *, preset_id: str) -> str:
    return render_preset_body(preset_body(preset_id, config.language), config)


async def build_companion_environment_prompt(db: AsyncSession, user_id: int, *, language: str) -> str:
    state = await get_scene_state(db, user_id)
    parts: list[str] = []
    # 此刻着装：有场景时以场景成品描述中的可见造型为准，无场景才注入当前着装；两种来源都附着装与表现相称的说明。
    source = "scene" if state.active is not None else None
    if source is None and (outfit := await build_outfit_extras(db, user_id, language=language)):
        parts.append(outfit)
        source = "outfit"
    if source is not None:
        parts.append(
            resolve_prompt_text(OUTFIT_DEMEANOR_GUIDANCES, language).replace(
                "{source}",
                OUTFIT_SOURCE_TEXTS[resolve_language(language)][source],
            ),
        )
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
