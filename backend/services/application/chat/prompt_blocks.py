"""系统提示词块渲染器注册表。

``substitute`` 在 ``preset.body`` 上严格替换 ``{{BLOCK_NAME}}`` 占位符：未识别 → logger.warning + 原文保留；空值 → 替换成空串后用 ``_collapse_blanks`` 收紧连续空行。
"""

import logging
import re
from collections.abc import Callable

from components import (
    TOOL_ENFORCE_OFF_VALUES,
    format_local_date_str,
    resolve_language,
    resolve_prompt_text,
    utc_now,
)
from modules.system import AgentPromptConfig
from prompts.chat import (
    AGENT_IDENTITIES,
    ATTACHMENT_GUIDANCES,
    AUTOMATION_GUIDANCES,
    COMPANION_CHAT_GUIDANCES,
    COMPANION_CONTEXT_GUIDANCES,
    COMPANION_DESKTOP_HINTS,
    COMPANION_OUTPUT_GUIDANCES,
    COMPANION_PROACTIVE_GUIDANCES,
    COMPANION_PROACTIVE_WAIT_GUIDANCES,
    COMPANION_RECALL_GUIDANCES,
    COMPANION_SELF_MEDIA_GUIDANCES,
    COMPANION_SKILL_GUIDANCES,
    COMPANION_TOOL_GUIDANCES,
    COMPANION_WAIT_GUIDANCES,
    LANGUAGE_DIRECTIVES,
    MEDIA_GUIDANCES,
    MEDIA_VIDEO_GUIDANCES,
    MEMORY_RECALL_GUIDANCES,
    MEMORY_TOOL_GUIDANCES,
    NO_TOOL_GUIDANCES,
    OUTFIT_DEMEANOR_GUIDANCES,
    PLATFORM_HINTS_TEXTS,
    SESSION_SEARCH_GUIDANCES,
    TOOL_USE_ENFORCEMENTS,
    VOLATILE_LABELS,
    WORK_GUIDANCES,
    WORK_SKILLS_GUIDANCES,
    WORK_TOOL_GUIDANCES,
)

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z][A-Z0-9_]{2,40})\}\}")


def _should_inject_tool_use_enforcement(setting: str) -> bool:
    """``tool_use_enforcement`` 除非显式关闭，否则视为开启。"""
    return setting.lower() not in TOOL_ENFORCE_OFF_VALUES


def _format_volatile_header(config: AgentPromptConfig) -> str:
    lang = resolve_language(config.language)
    label = resolve_prompt_text(VOLATILE_LABELS, lang)
    date_str = format_local_date_str(utc_now(), config.user_local_tz, lang)
    return f"{label}{date_str or ''}"


def _persona_block(config: AgentPromptConfig) -> str | None:
    return config.persona_extras or None


def _companion_chat_guidance_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(COMPANION_CHAT_GUIDANCES, config.language)


def _companion_context_guidance_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(COMPANION_CONTEXT_GUIDANCES, config.language)


def _companion_output_guidance_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(COMPANION_OUTPUT_GUIDANCES, config.language)


def _companion_tool_guidance_block(config: AgentPromptConfig) -> str | None:
    if not config.valid_tool_names:
        return resolve_prompt_text(NO_TOOL_GUIDANCES, config.language)
    parts: list[str] = []
    if _should_inject_tool_use_enforcement(config.tool_use_enforcement):
        parts.append(resolve_prompt_text(COMPANION_TOOL_GUIDANCES, config.language))
    if "companion_wait" in config.valid_tool_names:
        parts.append(resolve_prompt_text(COMPANION_WAIT_GUIDANCES, config.language))
    if "session_search" in config.valid_tool_names:
        parts.append(resolve_prompt_text(COMPANION_RECALL_GUIDANCES, config.language))
    if "skills_list" in config.valid_tool_names:
        parts.append(resolve_prompt_text(COMPANION_SKILL_GUIDANCES, config.language))
    return "\n".join(parts) or None


def _companion_proactive_guidance_block(config: AgentPromptConfig) -> str | None:
    if not config.companion_proactive_turn:
        return None
    parts: list[str] = [resolve_prompt_text(COMPANION_PROACTIVE_GUIDANCES, config.language)]
    if "companion_wait" in config.valid_tool_names:
        parts.append(resolve_prompt_text(COMPANION_PROACTIVE_WAIT_GUIDANCES, config.language))
    return "\n".join(parts)


def _outfit_block(config: AgentPromptConfig) -> str | None:
    if not config.outfit_extras:
        return None
    return f"{config.outfit_extras}\n\n{resolve_prompt_text(OUTFIT_DEMEANOR_GUIDANCES, config.language)}"


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
    parts: list[str] = []
    if "memory_recall" in config.valid_tool_names:
        parts.append(resolve_prompt_text(MEMORY_RECALL_GUIDANCES, config.language))
    if {"memory_inspect", "memory_retain"}.issubset(config.valid_tool_names):
        parts.append(resolve_prompt_text(MEMORY_TOOL_GUIDANCES, config.language))
    return "\n".join(parts) or None


def _session_search_guidance_block(config: AgentPromptConfig) -> str | None:
    return (
        resolve_prompt_text(SESSION_SEARCH_GUIDANCES, config.language)
        if "session_search" in config.valid_tool_names
        else None
    )


def _automation_guidance_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(AUTOMATION_GUIDANCES, config.language)


def _work_skills_guidance_block(config: AgentPromptConfig) -> str | None:
    return (
        resolve_prompt_text(WORK_SKILLS_GUIDANCES, config.language)
        if _has_any_tool(config, ("skills_list", "skill_view", "skill_manage"))
        else None
    )


def _media_guidance_block(config: AgentPromptConfig) -> str | None:
    if not _has_any_tool(config, ("image_generate", "video_generate")):
        return None
    parts = [resolve_prompt_text(MEDIA_GUIDANCES, config.language)]
    if "video_generate" in config.valid_tool_names:
        parts.append(resolve_prompt_text(MEDIA_VIDEO_GUIDANCES, config.language))
    return "\n".join(parts)


def _companion_media_guidance_block(config: AgentPromptConfig) -> str | None:
    base = _media_guidance_block(config)
    if base is None:
        return None
    return f"{base}\n{resolve_prompt_text(COMPANION_SELF_MEDIA_GUIDANCES, config.language)}"


def _attachment_guidance_block(config: AgentPromptConfig) -> str | None:
    return (
        resolve_prompt_text(ATTACHMENT_GUIDANCES, config.language) if "read_file" in config.valid_tool_names else None
    )


def _tool_use_enforcement_block(config: AgentPromptConfig) -> str | None:
    if not config.valid_tool_names:
        return resolve_prompt_text(NO_TOOL_GUIDANCES, config.language)
    return (
        resolve_prompt_text(TOOL_USE_ENFORCEMENTS, config.language)
        if _should_inject_tool_use_enforcement(config.tool_use_enforcement)
        else None
    )


def _work_tool_guidance_block(config: AgentPromptConfig) -> str | None:
    if not config.valid_tool_names:
        return resolve_prompt_text(NO_TOOL_GUIDANCES, config.language)
    return (
        resolve_prompt_text(WORK_TOOL_GUIDANCES, config.language)
        if _should_inject_tool_use_enforcement(config.tool_use_enforcement)
        else None
    )


def _environment_hints_block(config: AgentPromptConfig) -> str | None:
    ctx = config.client_context
    return ctx.environment_hints if ctx and ctx.environment_hints else None


def _platform_hints_block(config: AgentPromptConfig) -> str | None:
    ctx = config.client_context
    if ctx and ctx.platform_hints:
        return ctx.platform_hints
    platform_key = (config.platform or "").lower().strip()
    if platform_key in ("weixin", "weixin_ilink"):
        platform_key = "wechat"
    platform_dict = PLATFORM_HINTS_TEXTS.get(platform_key)
    if platform_dict is None:
        return None
    return resolve_prompt_text(platform_dict, config.language)


def _companion_platform_hints_block(config: AgentPromptConfig) -> str | None:
    ctx = config.client_context
    if ctx and ctx.platform_hints:
        return ctx.platform_hints
    if (config.platform or "").lower().strip() == "desktop":
        return resolve_prompt_text(COMPANION_DESKTOP_HINTS, config.language)
    return _platform_hints_block(config)


def _user_identity_override_block(config: AgentPromptConfig) -> str:
    if config.identity_prompt:
        return config.identity_prompt
    return resolve_prompt_text(AGENT_IDENTITIES, config.language)


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
            "每天首条消息前的日期分界线标明本地日，用户消息后的时间提示标明时刻与距上一轮的间隔。"
            f"它们是系统元数据，不是用户发言；发言方以消息角色为准。{tz_note}\n"
            "用这些线索区分连续聊天与隔段时间后的重逢，避免每轮重新问候。"
            "间隔只能说明时间经过，不能据此认定用户的经历、作息或离开原因。"
        )
    tz_note = (
        f" (user local timezone: {config.user_local_tz})"
        if config.user_local_tz
        else " (user local timezone not set; times are server UTC)"
    )
    return (
        "## Time Perception\n"
        "A date divider before the first message of each local day gives the date; notes following user "
        "messages give clock time and elapsed interval. These are system metadata, not user speech; "
        f"identify speakers by message role.{tz_note}\n"
        "Use these cues to distinguish an ongoing exchange from reconnecting after time apart, without "
        "greeting anew every turn. An interval shows elapsed time, not the user's experiences, habits, "
        "or reason for leaving."
    )


def _language_directive_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(LANGUAGE_DIRECTIVES, config.language)


def _work_guidance_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(WORK_GUIDANCES, config.language)


def _volatile_header_block(config: AgentPromptConfig) -> str:
    return _format_volatile_header(config)


BLOCK_RENDERERS: dict[str, Callable[[AgentPromptConfig], str | None]] = {
    "AUTOMATION_GUIDANCE": _automation_guidance_block,
    "LANGUAGE_DIRECTIVE": _language_directive_block,
    "WORK_GUIDANCE": _work_guidance_block,
    "WORK_TOOL_GUIDANCE": _work_tool_guidance_block,
    "WORK_SKILLS_GUIDANCE": _work_skills_guidance_block,
    "COMPANION_PERSONA": _persona_block,
    "COMPANION_CHAT_GUIDANCE": _companion_chat_guidance_block,
    "COMPANION_CONTEXT_GUIDANCE": _companion_context_guidance_block,
    "COMPANION_OUTPUT_GUIDANCE": _companion_output_guidance_block,
    "COMPANION_PROACTIVE_GUIDANCE": _companion_proactive_guidance_block,
    "COMPANION_TOOL_GUIDANCE": _companion_tool_guidance_block,
    "COMPANION_MEDIA_GUIDANCE": _companion_media_guidance_block,
    "COMPANION_PLATFORM_HINTS": _companion_platform_hints_block,
    "OUTFIT": _outfit_block,
    "USER_PROFILE": _config_attr_block("user_profile_extras"),
    "BACKGROUND_MEMORY": _config_attr_block("background_memory_extras"),
    "PROACTIVE_MEMORY": _config_attr_block("proactive_memory_extras"),
    "MEMORY_TOOL_GUIDANCE": _memory_tool_guidance_block,
    "SESSION_SEARCH_GUIDANCE": _session_search_guidance_block,
    "MEDIA_GUIDANCE": _media_guidance_block,
    "ATTACHMENT_GUIDANCE": _attachment_guidance_block,
    "TOOL_USE_ENFORCEMENT": _tool_use_enforcement_block,
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
