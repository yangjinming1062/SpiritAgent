import re

from components import format_local_date_str, resolve_language, utc_now
from modules.system import AgentPromptConfig, PromptPreset

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


def refresh_volatile_header_in_prompt(
    instructions: str,
    *,
    user_local_tz: str | None,
    lang: str,
) -> str:
    """发送前最后一刻刷新 volatile header 行的日期部分，保留原 label。

    设计取舍：只刷日期这一行；persona / outfit / native_memory 等由 per-turn
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
