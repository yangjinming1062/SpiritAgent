"""系统提示词块渲染器注册表。``render_preset_body`` 严格替换 ``{{BLOCK_NAME}}``：未识别则 warning 并保留原文；空值替换为空串后收紧连续空行。"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from components import DEFAULT_LANGUAGE, format_local_date_str, resolve_language, resolve_prompt_text, utc_now
from modules.auth import ChatRequestClientContext
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
    PLATFORM_HINTS_TEXTS,
    SCENE_TOOL_GUIDANCES,
    SESSION_SEARCH_GUIDANCES,
    TOOL_USE_ENFORCEMENTS,
    VOLATILE_LABELS,
    WORK_GUIDANCES,
    WORK_SKILLS_GUIDANCES,
    WORK_TOOL_GUIDANCES,
)

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z][A-Z0-9_]{2,40})\}\}")
# IM 适配器以渠道键声明平台（见 channels.base.platform_hint），桌面客户端传入的是自由文本。
_CHANNEL_HINT_KEYS = {"weixin": "wechat"}


@dataclass(frozen=True)
class AgentPromptConfig:
    """一次系统提示词渲染所需的资料；工具名决定能力说明，其余字段为身份、记忆与环境资料。"""

    language: str = DEFAULT_LANGUAGE
    valid_tool_names: list[str] = field(default_factory=list)
    companion_proactive_turn: bool = False
    client_context: ChatRequestClientContext | None = None
    persona_extras: str | None = None
    user_profile_extras: str | None = None
    background_memory_extras: str = ""
    proactive_memory_extras: str = ""
    # 用户本地 IANA 时区；None 表示未设置，日期按服务端 UTC。
    user_local_tz: str | None = None


def _volatile_header_block(config: AgentPromptConfig) -> str:
    lang = resolve_language(config.language)
    date_str = format_local_date_str(utc_now(), config.user_local_tz, lang)
    return f"{resolve_prompt_text(VOLATILE_LABELS, lang)}{date_str or ''}"


def _persona_block(config: AgentPromptConfig) -> str | None:
    return config.persona_extras or None


def _companion_chat_guidance_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(COMPANION_CHAT_GUIDANCES, config.language)


def _companion_context_guidance_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(COMPANION_CONTEXT_GUIDANCES, config.language)


def _companion_output_guidance_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(COMPANION_OUTPUT_GUIDANCES, config.language)


def _companion_tool_guidance_block(config: AgentPromptConfig) -> str:
    if not config.valid_tool_names:
        return resolve_prompt_text(NO_TOOL_GUIDANCES, config.language)
    parts = [resolve_prompt_text(COMPANION_TOOL_GUIDANCES, config.language)]
    if "companion_wait" in config.valid_tool_names:
        parts.append(resolve_prompt_text(COMPANION_WAIT_GUIDANCES, config.language))
    if "session_search" in config.valid_tool_names:
        parts.append(resolve_prompt_text(COMPANION_RECALL_GUIDANCES, config.language))
    if "skills_list" in config.valid_tool_names:
        parts.append(resolve_prompt_text(COMPANION_SKILL_GUIDANCES, config.language))
    if {"scene_list", "scene_create", "scene_activate"}.issubset(config.valid_tool_names):
        parts.append(resolve_prompt_text(SCENE_TOOL_GUIDANCES, config.language))
    return "\n".join(parts)


def _companion_proactive_guidance_block(config: AgentPromptConfig) -> str | None:
    if not config.companion_proactive_turn:
        return None
    parts: list[str] = [resolve_prompt_text(COMPANION_PROACTIVE_GUIDANCES, config.language)]
    if "companion_wait" in config.valid_tool_names:
        parts.append(resolve_prompt_text(COMPANION_PROACTIVE_WAIT_GUIDANCES, config.language))
    return "\n".join(parts)


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


def _tool_use_enforcement_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(
        TOOL_USE_ENFORCEMENTS if config.valid_tool_names else NO_TOOL_GUIDANCES,
        config.language,
    )


def _work_tool_guidance_block(config: AgentPromptConfig) -> str:
    return resolve_prompt_text(WORK_TOOL_GUIDANCES if config.valid_tool_names else NO_TOOL_GUIDANCES, config.language)


def _environment_hints_block(config: AgentPromptConfig) -> str | None:
    ctx = config.client_context
    return ctx.environment_hints if ctx and ctx.environment_hints else None


def _channel_hints(config: AgentPromptConfig, desktop_hints: dict[str, str]) -> str:
    hints = config.client_context.platform_hints if config.client_context else None
    if not hints:
        return resolve_prompt_text(desktop_hints, config.language)
    key = _CHANNEL_HINT_KEYS.get(hints.strip().lower())
    return resolve_prompt_text(PLATFORM_HINTS_TEXTS[key], config.language) if key else hints


def _platform_hints_block(config: AgentPromptConfig) -> str:
    return _channel_hints(config, PLATFORM_HINTS_TEXTS["desktop"])


def _companion_platform_hints_block(config: AgentPromptConfig) -> str:
    return _channel_hints(config, COMPANION_DESKTOP_HINTS)


def _agent_identity_block(config: AgentPromptConfig) -> str:
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
    "USER_PROFILE": lambda config: config.user_profile_extras or None,
    "BACKGROUND_MEMORY": lambda config: config.background_memory_extras or None,
    "PROACTIVE_MEMORY": lambda config: config.proactive_memory_extras or None,
    "MEMORY_TOOL_GUIDANCE": _memory_tool_guidance_block,
    "SESSION_SEARCH_GUIDANCE": _session_search_guidance_block,
    "MEDIA_GUIDANCE": _media_guidance_block,
    "ATTACHMENT_GUIDANCE": _attachment_guidance_block,
    "TOOL_USE_ENFORCEMENT": _tool_use_enforcement_block,
    "ENVIRONMENT_HINTS": _environment_hints_block,
    "PLATFORM_HINTS": _platform_hints_block,
    "AGENT_IDENTITY": _agent_identity_block,
    "VOLATILE_HEADER": _volatile_header_block,
    "MESSAGE_TIMESTAMPS": _message_timestamps_block,
}


def render_preset_body(body: str, config: AgentPromptConfig) -> str:
    """只渲染预设体实际引用的块；白名单外的占位符原文保留并告警，空值替换成空串。"""

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        renderer = BLOCK_RENDERERS.get(name)
        if renderer is None:
            logger.warning("unknown prompt placeholder %s in preset body", name)
            return match.group(0)
        return renderer(config) or ""

    return re.sub(r"\n{3,}", "\n\n", PLACEHOLDER_PATTERN.sub(_replace, body)).strip()
