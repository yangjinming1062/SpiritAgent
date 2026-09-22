"""显式装配注册。

供应商、LLM 工具、memory 工具、渠道适配器与内部事件处理器全部在此集中登记；
业务包导入不再触发任何注册副作用。注册表为覆盖式幂等，重复调用安全。
"""

from modules.ws import COMPANION_TURN_EVENT
from services.adapters.channels import register as register_channel
from services.adapters.channels.adapters import WeixinIlinkAdapter
from services.adapters.tools import agent_delegate, cronjob_tool, search_tools_tool
from services.adapters.tools import memory as memory_tools
from services.adapters.tools.builtin import (
    register_actions,
    register_companion_wait,
    register_image_generation,
    register_journal,
    register_scene,
    register_send_message,
    register_video_generation,
    register_web,
)
from services.application.automation import execute_companion_turn
from services.domains.automation import set_job_intent_invalidator
from services.domains.companion import (
    invalidate_cron_companion_intents,
)
from services.infrastructure.event_store import register_internal_event_handler
from services.infrastructure.llm import ServiceType
from services.infrastructure.llm import register as register_provider
from services.infrastructure.llm.providers import gemini, grok, mimo, minimax
from services.infrastructure.tool_runtime import REGISTRY


def register_providers() -> None:
    register_provider(ServiceType.image_gen, "gemini", gemini.GeminiImageGenProvider)
    register_provider(ServiceType.embedding, "gemini", gemini.GeminiEmbeddingProvider)
    register_provider(ServiceType.llm, "grok", grok.GrokChatProvider)
    register_provider(ServiceType.stt, "grok", grok.GrokSTTProvider)
    register_provider(ServiceType.tts, "grok", grok.GrokTTSProvider)
    register_provider(ServiceType.image_gen, "grok", grok.GrokImageGenProvider)
    register_provider(ServiceType.video_gen, "grok", grok.GrokVideoGenProvider)
    register_provider(ServiceType.llm, "mimo", mimo.MiMoChatProvider)
    register_provider(ServiceType.stt, "mimo", mimo.MiMoSTTProvider)
    register_provider(ServiceType.tts, "mimo", mimo.MiMoTTSProvider)
    register_provider(ServiceType.llm, "minimax", minimax.MiniMaxChatProvider)
    register_provider(ServiceType.image_gen, "minimax", minimax.MiniMaxImageGenProvider)
    register_provider(ServiceType.video_gen, "minimax", minimax.MiniMaxVideoGenProvider)
    register_provider(ServiceType.tts, "minimax", minimax.MiniMaxTTSProvider)
    register_provider(ServiceType.stt, "minimax", minimax.MiniMaxSTTProvider)
    register_provider(ServiceType.embedding, "minimax", minimax.MiniMaxEmbeddingProvider)


def register_tools() -> None:
    memory_tools.register_memory_tools(REGISTRY)
    search_tools_tool.register(REGISTRY)
    register_actions(REGISTRY)
    register_companion_wait(REGISTRY)
    register_image_generation(REGISTRY)
    register_journal(REGISTRY)
    register_scene(REGISTRY)
    register_send_message(REGISTRY)
    register_video_generation(REGISTRY)
    register_web(REGISTRY)
    cronjob_tool.register(REGISTRY)
    agent_delegate.register_delegate_tool(REGISTRY)


def register_channel_adapters() -> None:
    register_channel("weixin_ilink", WeixinIlinkAdapter)


def register_internal_event_handlers() -> None:
    register_internal_event_handler(COMPANION_TURN_EVENT, execute_companion_turn)


def wire_domain_hooks() -> None:
    """业务域内需要触达生成流程或跨域副作用的少数入口，经装配层注入，保持域间依赖可登记、可检查。"""
    set_job_intent_invalidator(invalidate_cron_companion_intents)


def register_all() -> None:
    register_providers()
    register_tools()
    register_channel_adapters()
    register_internal_event_handlers()
    wire_domain_hooks()
