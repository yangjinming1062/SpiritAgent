"""显式装配注册。

供应商、LLM 工具、memory 工具、渠道适配器与内部事件处理器全部在此集中登记；
业务包导入不再触发任何注册副作用。注册表为覆盖式幂等，重复调用安全。
"""

from modules.ws import CRON_TURN_EVENT
from services.adapters.channels.adapters.weixin_ilink import WeixinIlinkAdapter
from services.adapters.channels.registry import register as register_channel
from services.adapters.tools import agent_delegate, cronjob_tool, search_tools_tool
from services.adapters.tools import memory as memory_tools
from services.adapters.tools.builtin.image_generation_tool import register as register_image_generation
from services.adapters.tools.builtin.journal_tool import register as register_journal
from services.adapters.tools.builtin.room_backdrop_tool import register as register_room_backdrop
from services.adapters.tools.builtin.send_message_tool import register as register_send_message
from services.adapters.tools.builtin.video_generation_tool import register as register_video_generation
from services.adapters.tools.builtin.web_tools import register as register_web
from services.application.automation.cron_turns import execute_cron_turn
from services.application.generation.room_backdrop_service import schedule_initial_room
from services.domains.companion.persona_service import set_initial_room_scheduler
from services.infrastructure.event_store import register_internal_event_handler
from services.infrastructure.image_to_3d.providers import HunyuanImageTo3DProvider, TripoImageTo3DProvider
from services.infrastructure.image_to_3d.registry import register as register_image_to_3d_provider
from services.infrastructure.llm.providers import gemini, grok, mimo, minimax, zhipu
from services.infrastructure.llm.providers.base import ServiceType
from services.infrastructure.llm.providers.registry import register as register_provider
from services.infrastructure.tool_runtime import REGISTRY


def register_providers() -> None:
    register_image_to_3d_provider("hunyuan", HunyuanImageTo3DProvider)
    register_image_to_3d_provider("tripo", TripoImageTo3DProvider)
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
    register_provider(ServiceType.image_gen, "mimo", mimo.MiMoImageGenProvider)
    register_provider(ServiceType.llm, "minimax", minimax.MiniMaxChatProvider)
    register_provider(ServiceType.image_gen, "minimax", minimax.MiniMaxImageGenProvider)
    register_provider(ServiceType.video_gen, "minimax", minimax.MiniMaxVideoGenProvider)
    register_provider(ServiceType.tts, "minimax", minimax.MiniMaxTTSProvider)
    register_provider(ServiceType.embedding, "minimax", minimax.MiniMaxEmbeddingProvider)
    register_provider(ServiceType.stt, "zhipu", zhipu.ZhipuSTTProvider)
    register_provider(ServiceType.tts, "zhipu", zhipu.ZhipuTTSProvider)
    register_provider(ServiceType.image_gen, "zhipu", zhipu.ZhipuImageGenProvider)
    register_provider(ServiceType.embedding, "zhipu", zhipu.ZhipuEmbeddingProvider)


def register_tools() -> None:
    memory_tools.register_memory_tools(REGISTRY)
    search_tools_tool.register(REGISTRY)
    register_image_generation(REGISTRY)
    register_journal(REGISTRY)
    register_room_backdrop(REGISTRY)
    register_send_message(REGISTRY)
    register_video_generation(REGISTRY)
    register_web(REGISTRY)
    cronjob_tool.register(REGISTRY)
    agent_delegate.register_delegate_tool(REGISTRY)


def register_channel_adapters() -> None:
    register_channel("weixin_ilink", WeixinIlinkAdapter)


def register_internal_event_handlers() -> None:
    register_internal_event_handler(CRON_TURN_EVENT, execute_cron_turn)


def wire_domain_hooks() -> None:
    """业务域内需要触达生成流程的少数副作用点，经装配层注入，避免域反向依赖应用。"""
    set_initial_room_scheduler(schedule_initial_room)


def register_all() -> None:
    register_providers()
    register_tools()
    register_channel_adapters()
    register_internal_event_handlers()
    wire_domain_hooks()
