"""系统提示词块渲染器注册表。

``substitute`` 在 ``preset.body`` 上严格替换 ``{{BLOCK_NAME}}`` 占位符：未识别 → logger.warning + 原文保留；空值 → 替换成空串后用 ``_collapse_blanks`` 收紧连续空行。
"""

import logging
import re
from collections.abc import Callable

from components import resolve_language, resolve_prompt_text
from modules.system import AgentPromptConfig

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z][A-Z0-9_]{2,40})\}\}")


def _persona_block(config: AgentPromptConfig) -> str | None:
    return config.persona_extras or None


def _companion_chat_guidance_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _COMPANION_CHAT_GUIDANCES

    return resolve_prompt_text(_COMPANION_CHAT_GUIDANCES, config.language)


def _outfit_block(config: AgentPromptConfig) -> str | None:
    if not config.outfit_extras:
        return None
    from .system_prompt import _OUTFIT_DEMEANOR_GUIDANCES

    return f"{config.outfit_extras}\n\n{resolve_prompt_text(_OUTFIT_DEMEANOR_GUIDANCES, config.language)}"


def _config_attr_block(attr: str) -> Callable[[AgentPromptConfig], str | None]:
    def _fn(config: AgentPromptConfig) -> str | None:
        v = getattr(config, attr, None)
        if not v:
            return None
        return v if isinstance(v, str) else str(v)

    return _fn


def _has_any_tool(config: AgentPromptConfig, names: tuple[str, ...]) -> bool:
    return any(name in config.valid_tool_names for name in names)


def _memory_tool_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _MEMORY_TOOL_GUIDANCES

    return (
        resolve_prompt_text(_MEMORY_TOOL_GUIDANCES, config.language)
        if _has_any_tool(config, ("memory", "memory_retain", "memory_recall"))
        else None
    )


def _session_search_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _SESSION_SEARCH_GUIDANCES

    return (
        resolve_prompt_text(_SESSION_SEARCH_GUIDANCES, config.language)
        if "session_search" in config.valid_tool_names
        else None
    )


def _skills_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _SKILLS_GUIDANCES

    return (
        resolve_prompt_text(_SKILLS_GUIDANCES, config.language) if "skill_manage" in config.valid_tool_names else None
    )


def _media_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _MEDIA_GUIDANCES

    return (
        resolve_prompt_text(_MEDIA_GUIDANCES, config.language)
        if _has_any_tool(config, ("image_generate", "video_generate"))
        else None
    )


def _attachment_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _ATTACHMENT_GUIDANCES

    return resolve_prompt_text(_ATTACHMENT_GUIDANCES, config.language) if config.valid_tool_names else None


def _tool_use_enforcement_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _TOOL_USE_ENFORCEMENTS, _should_inject_tool_use_enforcement

    return (
        resolve_prompt_text(_TOOL_USE_ENFORCEMENTS, config.language)
        if config.valid_tool_names and _should_inject_tool_use_enforcement(config.tool_use_enforcement)
        else None
    )


def _steer_channel_note_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _STEER_CHANNEL_NOTES

    return resolve_prompt_text(_STEER_CHANNEL_NOTES, config.language) if config.valid_tool_names else None


def _skills_list_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _SKILLS_LIST_TEXTS

    ctx = config.client_context
    if not ctx or not ctx.skills:
        return None
    skills = ", ".join(ctx.skills)
    return resolve_prompt_text(_SKILLS_LIST_TEXTS, config.language).format(skills=skills)


def _environment_hints_block(config: AgentPromptConfig) -> str | None:
    ctx = config.client_context
    return ctx.environment_hints if ctx and ctx.environment_hints else None


def _platform_hints_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _PLATFORM_HINTS_TEXTS

    ctx = config.client_context
    if ctx and ctx.platform_hints:
        return ctx.platform_hints
    platform_key = (config.platform or "").lower().strip()
    if platform_key in ("weixin", "weixin_ilink"):
        platform_key = "wechat"
    platform_dict = _PLATFORM_HINTS_TEXTS.get(platform_key)
    if platform_dict is None:
        return None
    return resolve_prompt_text(platform_dict, config.language)


def _user_identity_override_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _AGENT_IDENTITIES

    if config.identity_prompt:
        return config.identity_prompt
    return resolve_prompt_text(_AGENT_IDENTITIES, config.language)


def _message_timestamps_block(config: AgentPromptConfig) -> str:
    """陪伴对话的时间感知说明。"""
    lang = resolve_language(config.language)
    if lang == "zh":
        tz_note = (
            f"（用户本地时区：{config.user_local_tz}）"
            if config.user_local_tz
            else "（用户未设置本地时区，时间按服务端 UTC）"
        )
        return (
            "## 时间感知\n"
            "日期只出现在分界线里：每个本地日的第一条消息前会有 `--- YYYY年M月D日 周X ---`。"
            "每条用户消息后面只跟时刻与距上一轮间隔，日期只看分界线。"
            "这些是系统只读元数据，不是用户说的话。"
            f"{tz_note}\n"
            "- 用分界线感知**日历日**，用时刻感知**时段**（清晨/白天/深夜）。\n"
            "- 用间隔感知**节奏**：刚刚连着聊，还是隔了几小时/几天才回来。\n"
            "- 发言方只看消息角色。\n"
            "\n"
            "## 输出约束\n"
            "回复必须直接是角色台词。"
            "不要输出时间提示或日期分界线——它们会被 TTS 读出来。"
        )
    tz_note = (
        f" (user local timezone: {config.user_local_tz})"
        if config.user_local_tz
        else " (user local timezone not set; times are server UTC)"
    )
    return (
        "## Time Perception\n"
        "The calendar date appears only in dividers placed before the first message of each local day "
        "(`--- Weekday, Month DD, YYYY ---`). "
        "Each user message is followed only by clock time and elapsed interval, not the date. "
        "These are read-only metadata, not user speech."
        f"{tz_note}\n"
        "- Use dividers for **calendar day**, clock notes for **time of day**.\n"
        "- Use elapsed interval for **cadence**: back-to-back vs returning after hours or days.\n"
        "- Identify speakers by message role.\n"
        "\n"
        "## Output Constraint\n"
        "Start immediately with character dialogue. "
        "Never emit time notes or date dividers — TTS would read them aloud."
    )


def _language_directive_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _language_directive

    return _language_directive(config.language)


def _help_guidance_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _HELP_GUIDANCES

    return resolve_prompt_text(_HELP_GUIDANCES, config.language)


def _volatile_header_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _format_volatile_header

    return _format_volatile_header(config)


BLOCK_RENDERERS: dict[str, Callable[[AgentPromptConfig], str | None]] = {
    "LANGUAGE_DIRECTIVE": _language_directive_block,
    "HELP_GUIDANCE": _help_guidance_block,
    "COMPANION_PERSONA": _persona_block,
    "COMPANION_CHAT_GUIDANCE": _companion_chat_guidance_block,
    "OUTFIT": _outfit_block,
    "USER_PROFILE": _config_attr_block("user_profile_extras"),
    "AUTO_INJECT": _config_attr_block("auto_inject_extras"),
    "INFERRED_PROFILE": _config_attr_block("inferred_profile_extras"),
    "PROACTIVE_MEMORY": _config_attr_block("proactive_memory_extras"),
    "MEMORY_TOOL_GUIDANCE": _memory_tool_guidance_block,
    "SESSION_SEARCH_GUIDANCE": _session_search_guidance_block,
    "SKILLS_GUIDANCE": _skills_guidance_block,
    "MEDIA_GUIDANCE": _media_guidance_block,
    "ATTACHMENT_GUIDANCE": _attachment_guidance_block,
    "TOOL_USE_ENFORCEMENT": _tool_use_enforcement_block,
    "STEER_CHANNEL_NOTE": _steer_channel_note_block,
    "SKILLS_LIST": _skills_list_block,
    "ENVIRONMENT_HINTS": _environment_hints_block,
    "PLATFORM_HINTS": _platform_hints_block,
    "USER_IDENTITY_OVERRIDE": _user_identity_override_block,
    "VOLATILE_HEADER": _volatile_header_block,
    "MESSAGE_TIMESTAMPS": _message_timestamps_block,
}


def _collapse_blanks(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def substitute(body: str, render_results: dict[str, str | None]) -> str:
    """严格解析 preset.body：白名单内块命中 → 替换；未识别 → 原文保留 + warning；空值 → 替换成空串。"""

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in BLOCK_RENDERERS:
            logger.warning("unknown prompt placeholder %s in preset body", name)
            return match.group(0)
        return render_results.get(name, "") or ""

    rendered = PLACEHOLDER_PATTERN.sub(_replace, body)
    return _collapse_blanks(rendered)
