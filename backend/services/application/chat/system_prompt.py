import json
import re

from components import format_local_date_str, resolve_language, resolve_prompt_text, utc_now
from modules.system import AgentPromptConfig, PromptPreset
from prompts.chat import OUTFIT_DEMEANOR_GUIDANCES, SCENE_CONTEXT_GUIDANCES
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.actions.context import build_action_context
from services.domains.companion import build_outfit_extras, get_scene_state, scene_environment

from .prompt_blocks import BLOCK_RENDERERS, substitute
from .prompt_presets import _build_body

# volatile header 行的发送前正则：保留 label 只换日期，避免 raw lang='fr' 解析后把英文 label 替换成中文。
_VOLATILE_HEADER_RE = re.compile(r"(?m)^(?P<label>当前日期：|Current date: )(?P<date>.*)$")


def build_system_prompt(
    config: AgentPromptConfig,
    *,
    preset: PromptPreset,
) -> str:
    """按 preset.body 顺序渲染提示词块。"""
    render_results: dict[str, str | None] = {name: renderer(config) for name, renderer in BLOCK_RENDERERS.items()}
    return substitute(_build_body(preset, config.language), render_results)


async def build_companion_environment_prompt(db: AsyncSession, user_id: int, *, language: str) -> str:
    state = await get_scene_state(db, user_id)
    parts: list[str] = []
    if state.active is None:
        outfit = await build_outfit_extras(db, user_id, language=language)
        if outfit:
            parts.extend([outfit, resolve_prompt_text(OUTFIT_DEMEANOR_GUIDANCES, language)])
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


def refresh_volatile_header_in_prompt(
    instructions: str,
    *,
    user_local_tz: str | None,
    lang: str,
) -> str:
    """发送前最后一刻刷新 volatile header 行的日期部分，保留原 label。

    设计取舍：只刷日期这一行；persona / native_memory 等由 per-turn
    重建覆盖，build→send 排队窗口内被改的概率可忽略——全量重渲染会破坏
    native_memory 注入且需多查 5 次库。保留 label 是为了防止 raw ``lang='fr'``
    解析后被错换成成中文/英文标签。
    """
    match = _VOLATILE_HEADER_RE.search(instructions)
    if match is None:
        return instructions
    resolved_lang = resolve_language(lang)
    date_str = format_local_date_str(utc_now(), user_local_tz, resolved_lang)
    fresh = f"{match.group('label')}{date_str or ''}"
    return _VOLATILE_HEADER_RE.sub(fresh, instructions, count=1)
