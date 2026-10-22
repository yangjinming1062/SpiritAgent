"""显式装配注册：供应商、工具、事件与域钩子在此集中登记；导入业务包无注册副作用，注册表覆盖式幂等。"""

from entrypoints.desktop.event_contracts import EVENT_CONTRACTS, gateway_event_audience, serialize_gateway_event
from entrypoints.desktop.handlers import terminate_remote_sessions, terminate_user_gateway
from entrypoints.tools import agent_delegate, cronjob_tool, search_tools_tool
from entrypoints.tools import memory as memory_tools
from entrypoints.tools.builtin.action_tool import register as register_actions
from entrypoints.tools.builtin.companion_wait_tool import register as register_companion_wait
from entrypoints.tools.builtin.image_generation_tool import register as register_image_generation
from entrypoints.tools.builtin.scene_tool import register as register_scene
from entrypoints.tools.builtin.send_message_tool import register as register_send_message
from entrypoints.tools.builtin.video_generation_tool import register as register_video_generation
from entrypoints.tools.builtin.web_tools import register as register_web
from features.action_library.ports import configure_action_library_ports
from features.actions.asset_retirement import delete_outfit_action_packs
from features.actions.video_queue import inspect_video_queue_owner as inspect_action_video_owner
from features.appearance.action_cleanup import set_outfit_action_cleanup
from features.appearance.character_card import character_snapshot_is_current, render_character_identity
from features.appearance.completion import set_character_ready_handler, set_outfit_ready_handler
from features.appearance.queries import active_identity_reference, appearance_readiness, confirmed_media_reference_paths
from features.automation.cron_jobs import set_job_intent_invalidator
from features.companion.appearance_ports import set_appearance_readiness_reader
from features.companion.intents import invalidate_cron_companion_intents
from features.configuration.queries import user_ai_config
from features.conversation.reply_media import deliver_video_result
from features.desktop_life.generation import set_desktop_update_handler
from features.desktop_life.ports import (
    set_desktop_action_design_handler,
    set_desktop_proposal_list_handler,
    set_desktop_proposal_query_handler,
    set_desktop_set_created_handler,
)
from features.desktop_life.video_queue import inspect_video_queue_owner as inspect_desktop_video_owner
from features.journal.journal_indexes import delete_diary_record
from features.media.delivery import set_video_result_handler
from features.media.reference_reader import MediaReferenceReaders, set_media_reference_readers
from features.media.video_jobs import inspect_video_queue_owner as inspect_media_video_owner
from features.media.video_queue.ports import configure_video_queue_ports
from features.media.video_queue.service import cancel_video_attempt
from features.memory.diary_hooks import set_diary_delete_handler
from infrastructure.ai.configuration_reader import set_configuration_reader
from infrastructure.ai.providers.base import ServiceType
from infrastructure.ai.providers.custom.chat import CustomChatProvider
from infrastructure.ai.providers.custom.embedding import CustomEmbeddingProvider
from infrastructure.ai.providers.deepseek.chat import DeepSeekChatProvider
from infrastructure.ai.providers.gemini.embedding import GeminiEmbeddingProvider
from infrastructure.ai.providers.gemini.image import GeminiImageGenProvider
from infrastructure.ai.providers.grok.chat import GrokChatProvider
from infrastructure.ai.providers.grok.image import GrokImageGenProvider
from infrastructure.ai.providers.grok.stt import GrokSTTProvider
from infrastructure.ai.providers.grok.tts import GrokTTSProvider
from infrastructure.ai.providers.grok.video import GrokVideoGenProvider
from infrastructure.ai.providers.local.chat import LocalChatProvider
from infrastructure.ai.providers.local.embedding import LocalEmbeddingProvider
from infrastructure.ai.providers.local.image import LocalImageGenProvider
from infrastructure.ai.providers.mimo.chat import MiMoChatProvider
from infrastructure.ai.providers.mimo.stt import MiMoSTTProvider
from infrastructure.ai.providers.mimo.tts import MiMoTTSProvider
from infrastructure.ai.providers.minimax.chat import MiniMaxChatProvider
from infrastructure.ai.providers.minimax.embedding import MiniMaxEmbeddingProvider
from infrastructure.ai.providers.minimax.image import MiniMaxImageGenProvider
from infrastructure.ai.providers.minimax.stt import MiniMaxSTTProvider
from infrastructure.ai.providers.minimax.tts import MiniMaxTTSProvider
from infrastructure.ai.providers.minimax.video import MiniMaxVideoGenProvider
from infrastructure.ai.providers.qwen.chat import QwenChatProvider
from infrastructure.ai.providers.qwen.embedding import QwenEmbeddingProvider
from infrastructure.ai.providers.qwen.image import QwenImageGenProvider
from infrastructure.ai.providers.qwen.stt import QwenSTTProvider
from infrastructure.ai.providers.qwen.tts import QwenTTSProvider
from infrastructure.ai.providers.qwen.video import QwenVideoGenProvider
from infrastructure.ai.providers.registry import register as register_provider
from runtime.channels import ChannelLifecycle, register_channel_lifecycle
from runtime.desktop.connection import configure_event_audience
from runtime.desktop.jsonrpc import configure_event_serializer
from runtime.events.contracts import COMPANION_TURN_EVENT
from runtime.events.loop import register_internal_event_handler
from runtime.tools.registry import REGISTRY
from workflows.action_library import (
    action_source_reader,
    action_variants_reader,
    design_shared_desktop_action,
    schedule_action_definition,
    shared_desktop_proposal,
    shared_desktop_proposals,
    synchronize_action_target,
    synchronize_desktop_set,
    update_desktop_actions,
)
from workflows.automation.companion_turns import execute_companion_turn
from workflows.onboarding.character_ready import complete_character_initialization
from workflows.outfit_actions import start_outfit_actions

from bootstrap.assets import register_asset_contributions


def register_providers() -> None:
    register_provider(ServiceType.llm, "deepseek", DeepSeekChatProvider)
    register_provider(ServiceType.image_gen, "gemini", GeminiImageGenProvider)
    register_provider(ServiceType.embedding, "gemini", GeminiEmbeddingProvider)
    register_provider(ServiceType.llm, "grok", GrokChatProvider)
    register_provider(ServiceType.stt, "grok", GrokSTTProvider)
    register_provider(ServiceType.tts, "grok", GrokTTSProvider)
    register_provider(ServiceType.image_gen, "grok", GrokImageGenProvider)
    register_provider(ServiceType.video_gen, "grok", GrokVideoGenProvider)
    register_provider(ServiceType.llm, "mimo", MiMoChatProvider)
    register_provider(ServiceType.stt, "mimo", MiMoSTTProvider)
    register_provider(ServiceType.tts, "mimo", MiMoTTSProvider)
    register_provider(ServiceType.llm, "minimax", MiniMaxChatProvider)
    register_provider(ServiceType.image_gen, "minimax", MiniMaxImageGenProvider)
    register_provider(ServiceType.video_gen, "minimax", MiniMaxVideoGenProvider)
    register_provider(ServiceType.tts, "minimax", MiniMaxTTSProvider)
    register_provider(ServiceType.stt, "minimax", MiniMaxSTTProvider)
    register_provider(ServiceType.embedding, "minimax", MiniMaxEmbeddingProvider)
    register_provider(ServiceType.llm, "qwen", QwenChatProvider)
    register_provider(ServiceType.stt, "qwen", QwenSTTProvider)
    register_provider(ServiceType.tts, "qwen", QwenTTSProvider)
    register_provider(ServiceType.image_gen, "qwen", QwenImageGenProvider)
    register_provider(ServiceType.video_gen, "qwen", QwenVideoGenProvider)
    register_provider(ServiceType.embedding, "qwen", QwenEmbeddingProvider)
    register_provider(ServiceType.llm, "local", LocalChatProvider)
    register_provider(ServiceType.embedding, "local", LocalEmbeddingProvider)
    register_provider(ServiceType.image_gen, "local", LocalImageGenProvider)
    register_provider(ServiceType.llm, "custom", CustomChatProvider)
    register_provider(ServiceType.embedding, "custom", CustomEmbeddingProvider)


def register_tools() -> None:
    memory_tools.register_memory_tools(REGISTRY)
    search_tools_tool.register(REGISTRY)
    register_actions(REGISTRY)
    register_companion_wait(REGISTRY)
    register_image_generation(REGISTRY)
    register_scene(REGISTRY)
    register_send_message(REGISTRY)
    register_video_generation(REGISTRY)
    register_web(REGISTRY)
    cronjob_tool.register(REGISTRY)
    agent_delegate.register_delegate_tool(REGISTRY)


def register_internal_event_handlers() -> None:
    register_internal_event_handler(COMPANION_TURN_EVENT, execute_companion_turn)


def wire_domain_hooks() -> None:
    """业务域内需要触达生成流程或跨域副作用的少数入口，经装配层注入，保持域间依赖可登记、可检查。"""
    configure_event_serializer(serialize_gateway_event)
    configure_event_audience(gateway_event_audience, event_types=EVENT_CONTRACTS)
    set_job_intent_invalidator(invalidate_cron_companion_intents)
    set_video_result_handler(deliver_video_result)
    register_asset_contributions()
    set_configuration_reader(user_ai_config)
    configure_video_queue_ports(
        {
            "media": inspect_media_video_owner,
            "action": inspect_action_video_owner,
            "desktop": inspect_desktop_video_owner,
        },
        cancel_video_attempt,
    )
    register_channel_lifecycle(
        ChannelLifecycle(close_desktop=terminate_user_gateway, close_remote=terminate_remote_sessions),
    )
    set_diary_delete_handler(delete_diary_record)
    set_media_reference_readers(
        MediaReferenceReaders(
            active_identity=active_identity_reference,
            confirmed_paths=confirmed_media_reference_paths,
            identity_is_current=character_snapshot_is_current,
            render_identity=render_character_identity,
        ),
    )
    set_character_ready_handler(complete_character_initialization)
    set_outfit_ready_handler(start_outfit_actions)
    configure_action_library_ports(
        source_reader=action_source_reader,
        variant_reader=action_variants_reader,
        definition_scheduler=schedule_action_definition,
        target_sync_handler=synchronize_action_target,
    )
    set_desktop_action_design_handler(design_shared_desktop_action)
    set_desktop_proposal_query_handler(shared_desktop_proposal)
    set_desktop_proposal_list_handler(shared_desktop_proposals)
    set_desktop_set_created_handler(synchronize_desktop_set)
    set_desktop_update_handler(update_desktop_actions)
    set_appearance_readiness_reader(appearance_readiness)
    set_outfit_action_cleanup(delete_outfit_action_packs)


def register_all() -> None:
    register_providers()
    register_tools()
    register_internal_event_handlers()
    wire_domain_hooks()
