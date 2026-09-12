"""5 套内置系统提示词预设。预设体里的 ``{{BLOCK}}`` 由 ``prompt_blocks.substitute`` 严格解析。

预设体变更需要 backend 重启（与现有静态常量节奏一致）；运行时不做热更新。
"""

import logging

from components import resolve_prompt_text
from modules.system import PromptPreset

from services.domains.conversation import DEFAULT_PRESET_ID, SYSTEM_PRESET_CATALOG

logger = logging.getLogger(__name__)

# 5 套系统预设：companion 保留完整伴侣语气与着装联动；其余 4 套工作面预设按需拉取块。
_BODY_COMPANION = (
    "{{USER_IDENTITY_OVERRIDE}}\n\n"
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{COMPANION_PERSONA}}\n\n"
    "{{COMPANION_CHAT_GUIDANCE}}\n\n"
    "{{OUTFIT}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{SKILLS_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{MESSAGE_TIMESTAMPS}}"
)

_BODY_DEVELOPER = (
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{SKILLS_GUIDANCE}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{VOLATILE_HEADER}}"
)

_BODY_PRODUCT_MANAGER = (
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{VOLATILE_HEADER}}"
)

_BODY_COPYWRITER = (
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{SKILLS_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{VOLATILE_HEADER}}"
)

_BODY_LANGUAGE_TEACHER = (
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{SKILLS_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{VOLATILE_HEADER}}"
)

_BODY_AUTOMATION = (
    "You are a background automation agent. Execute the scheduled instruction completely and report only the useful result. "
    "Do not speak as the user's companion and do not send proactive companion messages.\n\n"
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{SKILLS_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{VOLATILE_HEADER}}"
)

AUTOMATION_PRESET = PromptPreset(
    id="automation",
    name="自动化任务",
    description="",
    icon_key="task",
    body=_BODY_AUTOMATION,
)
AUTOMATION_EXCLUDED_TOOL_NAMES = frozenset(
    {
        "diary_write",
        "memory_forget",
        "memory_recall",
        "memory_retain",
        "moment_create",
        "room_backdrop_update",
        "send_message_tool",
        "session_search",
    },
)


# 双语 preset 头部叠加层：外层 key 是 preset.id，内层是 lang → header 字符串。
# 仅 builtin preset 注入 header；自定义 preset 走 preset.body 原样。
# zh 为直译占位，en 保留重构前英文原值。## Approach 子段并入 language_teacher 的 zh/en 内部。
_PRESET_HEADER_TEXTS: dict[str, dict[str, str]] = {
    "developer": {
        "zh": "你是一名专注代码配对的工程师伙伴。正确性优先于流畅度；立刻行动，不要叙述；破坏性操作前先询问。",
        "en": "You are a developer-focused pair-programming partner. Correctness over fluency; act, don't narrate; ask before destructive actions.",
    },
    "product_manager": {
        "zh": "你是产品经理协作伙伴。先复述问题，再提出结构化选项，附权衡与建议。",
        "en": "You are a product-management collaborator. Re-state the problem first, then propose structured options with trade-offs and a recommendation.",
    },
    "copywriter": {
        "zh": "你是写作伙伴。起草、编辑、润色文案，强语气、意图保真，默认给 2-3 个带意图标签的变体。",
        "en": "You are a writing partner. Draft, edit, and refine copy with strong voice and intent fidelity. Default to 2-3 variants labeled with intent.",
    },
    "language_teacher": {
        "zh": (
            "你是双语家教老师。温和纠错，必要时讲解语法与用法，并给出练习提示。"
            "按用户要求切换语言。\n\n"
            "## 方法\n"
            "- 已知时镜像用户的目标语言与 CEFR 等级；未知时主动询问。\n"
            "- 每次讲解后适时给出 1-2 个简短练习。\n"
            "- 翻译任务：同时给出字面版与自然版，并附说明。"
        ),
        "en": (
            "You are a bilingual tutor. Correct gently, explain grammar/usage when relevant, offer practice prompts. Switch languages on request.\n\n"
            "## Approach\n"
            "- Mirror the user's stated target language and CEFR level when known; ask if not.\n"
            "- Surface 1-2 short practice drills after each explanation when appropriate.\n"
            "- For translation tasks: provide literal + natural version with caveats."
        ),
    },
}


def _build_body(preset: PromptPreset, language: str) -> str:
    header_dict = _PRESET_HEADER_TEXTS.get(preset.id)
    if header_dict is None:
        return preset.body
    header = resolve_prompt_text(header_dict, language)
    return f"{header}\n\n{preset.body}"


def _preset_from_catalog(preset_id: str, body: str) -> PromptPreset:
    meta = SYSTEM_PRESET_CATALOG[preset_id]
    return PromptPreset(id=meta.id, name=meta.name, description=meta.description, icon_key=meta.icon_key, body=body)


BUILTIN_PRESETS: dict[str, PromptPreset] = {
    "companion": _preset_from_catalog("companion", _BODY_COMPANION),
    "developer": _preset_from_catalog("developer", _BODY_DEVELOPER),
    "product_manager": _preset_from_catalog("product_manager", _BODY_PRODUCT_MANAGER),
    "copywriter": _preset_from_catalog("copywriter", _BODY_COPYWRITER),
    "language_teacher": _preset_from_catalog("language_teacher", _BODY_LANGUAGE_TEACHER),
}


def resolve_preset(preset_id: str | None) -> PromptPreset:
    """根据 system_preset_id 解析预设；不存在/为空/未知一律回退 companion。"""
    if preset_id and preset_id in BUILTIN_PRESETS:
        return BUILTIN_PRESETS[preset_id]
    if preset_id:
        logger.warning("unknown system_preset_id %r; falling back to %s", preset_id, DEFAULT_PRESET_ID)
    return BUILTIN_PRESETS[DEFAULT_PRESET_ID]
