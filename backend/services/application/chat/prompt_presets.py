"""5 套内置系统提示词预设。预设体里的 ``{{BLOCK}}`` 由 ``prompt_blocks.substitute`` 严格解析。

预设体变更需要 backend 重启（与现有静态常量节奏一致）；运行时不做热更新。
"""

import logging

from components import resolve_prompt_text
from modules.system import PromptPreset

from services.domains.conversation import SYSTEM_PRESET_CATALOG

logger = logging.getLogger(__name__)

COMPANION_PREPARE_GUIDANCE: dict[str, str] = {
    "zh": (
        "# 当前阶段：内部准备\n"
        "这一阶段不向用户交付内容。判断回应最新消息还缺什么必要信息或操作，按需调用工具。"
        "已经足以回应，或剩余问题只能向用户说明限制、请求补充时，停止工具调用，只输出 READY。"
        "普通闲聊可以直接就绪，不预写台词或语音描述；下一阶段会根据准备结果正式回复。"
    ),
    "en": (
        "# Current phase: internal preparation\n"
        "Nothing from this phase is delivered to the user. Determine what information or actions are "
        "still needed to answer the latest message, and use tools as needed. Once ready, or when all that "
        "remains is to explain a limitation or ask the user for input, stop calling tools and output only "
        "READY. Casual chat can be ready immediately. Do not draft dialogue or speech metadata; the next "
        "phase will reply using the preparation results."
    ),
}
COMPANION_REPLY_GUIDANCE: dict[str, str] = {
    "zh": (
        "# 当前阶段：正式回复\n"
        "准备已结束，本阶段没有工具。结合用户最新表达、对话上下文与已经取得的结果，"
        "按上面的交付规则直接回应。若仍有信息缺口或操作未完成，如实说明必要的限制或请用户补充；"
        "不要复盘准备过程。"
    ),
    "en": (
        "# Current phase: final reply\n"
        "Preparation is complete and this phase has no tools. Respond directly to the user's latest "
        "message using the conversation and results obtained, following the delivery rules above. "
        "If information or work is still missing, explain the relevant limitation or ask for what is "
        "needed, without recapping the preparation process."
    ),
}

# 准备与回复共享事实背景及工具规则，聊天表达与正文格式只进入面向用户的补全。
_BODY_COMPANION_CONTEXT = (
    "{{USER_IDENTITY_OVERRIDE}}\n\n"
    "{{COMPANION_PERSONA}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{COMPANION_CONTEXT_GUIDANCE}}\n\n"
    "{{OUTFIT}}\n\n"
    "{{BACKGROUND_MEMORY}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{MESSAGE_TIMESTAMPS}}\n\n"
)
_BODY_COMPANION_TOOLS = (
    "{{COMPANION_TOOL_GUIDANCE}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{COMPANION_PLATFORM_HINTS}}\n\n"
)
_BODY_COMPANION = (
    _BODY_COMPANION_CONTEXT
    + "{{COMPANION_CHAT_GUIDANCE}}\n\n"
    + _BODY_COMPANION_TOOLS
    + "{{COMPANION_OUTPUT_GUIDANCE}}"
)

COMPANION_PREPARE_PRESET = PromptPreset(
    id="companion",
    name="陪伴内部准备",
    icon_key="companion",
    body=_BODY_COMPANION_CONTEXT + _BODY_COMPANION_TOOLS,
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
    "{{BACKGROUND_MEMORY}}\n\n"
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
    "{{BACKGROUND_MEMORY}}\n\n"
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
    "{{BACKGROUND_MEMORY}}\n\n"
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
    "{{BACKGROUND_MEMORY}}\n\n"
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
# 生活空间工具只服务陪伴会话：工作预设与自动化任务在回合装配层（build_turn_inputs）与
# search_tools 元工具同源过滤，压根不注入 schema，工具入口不再二次判定会话类型。
LIFE_SPACE_TOOL_NAMES = frozenset(
    {
        "send_message_tool",
        "diary_write",
        "moment_create",
        "room_backdrop_update",
    },
)
AUTOMATION_EXCLUDED_TOOL_NAMES = LIFE_SPACE_TOOL_NAMES | frozenset(
    {
        "memory_inspect",
        "memory_recall",
        "memory_retain",
        "session_search",
        "cronjob",
        "skills_list",
        "skill_view",
        "skill_manage",
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
    if preset_id not in BUILTIN_PRESETS:
        raise ValueError("Unknown system preset")
    return BUILTIN_PRESETS[preset_id]
