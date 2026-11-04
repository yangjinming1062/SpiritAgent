"""显式装配注册：供应商、工具、事件与域钩子在此集中登记；导入业务包无注册副作用，注册表覆盖式幂等。"""

from entrypoints.desktop.event_contracts import EVENT_CONTRACTS, gateway_event_audience, serialize_gateway_event
from entrypoints.desktop.handlers import terminate_remote_sessions, terminate_user_gateway
from entrypoints.tools import agent_delegate, cronjob_tool, search_tools_tool
from entrypoints.tools import memory as memory_tools
from entrypoints.tools.builtin.action_tool import register as register_actions
from entrypoints.tools.builtin.archive_tool import register as register_archive_tools
from entrypoints.tools.builtin.companion_wait_tool import register as register_companion_wait
from entrypoints.tools.builtin.image_generation_tool import register as register_image_generation
from entrypoints.tools.builtin.life_tool import register as register_life_tools
from entrypoints.tools.builtin.scene_tool import register as register_scene
from entrypoints.tools.builtin.send_message_tool import register as register_send_message
from entrypoints.tools.builtin.video_generation_tool import register as register_video_generation
from entrypoints.tools.builtin.visual_tool import register as register_visuals
from entrypoints.tools.builtin.web_tools import register as register_web
from features.appearance.avatar_service import fullbody_review_view, review_fullbody_job
from features.appearance.fullbody_reviews import manually_review_fullbody, run_manual_fullbody_job
from features.appearance.identity import visual_identity_is_current
from features.appearance.outfit_jobs import outfit_creation_continue_requested
from features.appearance.outfit_reviews import (
    continue_outfit_creation,
    manually_review_outfit,
    outfit_review_view,
    review_outfit_job,
    run_manual_outfit_job,
)
from features.appearance.queries import active_identity_reference, appearance_readiness, confirmed_media_reference_paths
from features.automation.cron_jobs import set_job_intent_invalidator
from features.companion.appearance_ports import set_appearance_readiness_reader
from features.companion.intents import invalidate_cron_companion_intents
from features.companion.scene_ports import set_initial_scene_ensurer
from features.configuration.queries import user_ai_config
from features.conversation.reply_media import (
    continue_chat_image,
    continue_chat_video,
    deliver_image_job_result,
    deliver_video_result,
)
from features.media.continuations import configure_continuation_handler, media_review_updated
from features.media.delivery import set_image_result_handler, set_video_result_handler
from features.media.image_jobs import image_job_view, manually_review_image, review_image_job, run_manual_image_job
from features.media.manual_reviews import configure_manual_handler
from features.media.reference_reader import MediaReferenceReaders, set_media_reference_readers
from features.media.review_operations import configure_review_handler
from features.media.video_jobs import inspect_video_queue_owner as inspect_media_video_owner
from features.media.video_jobs import manually_review_video, review_video_job, submit_video_job, video_review_view
from features.media.video_queue.ports import configure_video_queue_ports
from features.media.video_queue.service import cancel_video_attempt
from features.posts.ports import set_post_published_handler
from features.posts.publication import (
    manually_review_post,
    post_review_view,
    publication_continue_requested,
    resume_post_media,
    review_post_job,
    run_manual_post_job,
)
from features.scenes.queries import scene_exists
from features.scenes.reviews import manually_review_scene, review_scene_job, run_manual_scene_job, scene_review_view
from features.scenes.service import schedule_initial_scene
from features.visuals.import_reviews import import_review_view, manually_review_import, review_import_job
from features.visuals.ports import configure_visual_hooks
from features.visuals.reviews import (
    manually_review_visual,
    review_visual_job,
    run_manual_visual_job,
    visual_review_view,
)
from features.visuals.video_queue import inspect_visual_video_owner
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
from workflows.automation.companion_turns import execute_companion_turn
from workflows.life_events import record_post_published
from workflows.life_presentation import life_presentation_requested
from workflows.visual_decisions import initialize_visuals, schedule_visual_selection
from workflows.visual_imports import run_manual_import_job
from workflows.visual_planning import visual_planning_context
from workflows.visual_preparation import (
    continue_base_preparation,
    preparation_continue_requested,
    schedule_base_motion_preparation,
)

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
    register_visuals(REGISTRY)
    register_companion_wait(REGISTRY)
    register_life_tools(REGISTRY)
    register_archive_tools(REGISTRY)
    register_image_generation(REGISTRY)
    register_scene(REGISTRY)
    register_send_message(REGISTRY)
    register_video_generation(REGISTRY)
    register_web(REGISTRY)
    cronjob_tool.register(REGISTRY)
    agent_delegate.register_delegate_tool(REGISTRY)


def register_internal_event_handlers() -> None:
    register_internal_event_handler("media.review.updated", media_review_updated)
    register_internal_event_handler("post.publication.continue", publication_continue_requested)
    register_internal_event_handler("visual.preparation.continue", preparation_continue_requested)
    register_internal_event_handler("outfit.creation.continue", outfit_creation_continue_requested)
    register_internal_event_handler(COMPANION_TURN_EVENT, execute_companion_turn)
    register_internal_event_handler("companion.visual.initial_requested", initialize_visuals)
    register_internal_event_handler("companion.life.presentation_requested", life_presentation_requested)


def register_all() -> None:
    """登记供应商、工具与内部事件；并注入业务域需触达生成流程或跨域副作用的少数入口，保持域间依赖可登记、可检查。"""
    register_providers()
    register_tools()
    register_internal_event_handlers()
    configure_event_serializer(serialize_gateway_event)
    configure_event_audience(gateway_event_audience, event_types=EVENT_CONTRACTS)
    set_job_intent_invalidator(invalidate_cron_companion_intents)
    set_video_result_handler(deliver_video_result)
    set_image_result_handler(deliver_image_job_result)
    configure_review_handler("media.image", image_job_view, review_image_job)
    configure_manual_handler("media.image", image_job_view, manually_review_image, run_manual_image_job)
    configure_manual_handler("media.video", video_review_view, manually_review_video, submit_video_job)
    configure_manual_handler("outfit.create", outfit_review_view, manually_review_outfit, run_manual_outfit_job)
    configure_manual_handler("scene.generate", scene_review_view, manually_review_scene, run_manual_scene_job)
    configure_manual_handler("post.publish", post_review_view, manually_review_post, run_manual_post_job)
    configure_manual_handler("visual.generate", visual_review_view, manually_review_visual, run_manual_visual_job)
    configure_manual_handler(
        "appearance.fullbody_review", fullbody_review_view, manually_review_fullbody, run_manual_fullbody_job
    )
    configure_manual_handler("visual.import", import_review_view, manually_review_import, run_manual_import_job)
    configure_review_handler("media.video", video_review_view, review_video_job)
    configure_review_handler("visual.generate", visual_review_view, review_visual_job)
    configure_review_handler("visual.import", import_review_view, review_import_job)
    configure_review_handler("scene.generate", scene_review_view, review_scene_job)
    configure_review_handler("post.publish", post_review_view, review_post_job)
    configure_review_handler("outfit.create", outfit_review_view, review_outfit_job)
    configure_review_handler("appearance.fullbody_review", fullbody_review_view, review_fullbody_job)
    configure_continuation_handler("outfit.create", continue_outfit_creation)
    configure_continuation_handler("visuals.preparation", continue_base_preparation)
    configure_continuation_handler("post.publish", resume_post_media)
    configure_continuation_handler("chat.image", continue_chat_image)
    configure_continuation_handler("chat.video", continue_chat_video)
    register_asset_contributions()
    set_configuration_reader(user_ai_config)
    configure_video_queue_ports(
        {
            "media": inspect_media_video_owner,
            "visuals": inspect_visual_video_owner,
        },
        cancel_video_attempt,
    )
    register_channel_lifecycle(
        ChannelLifecycle(close_desktop=terminate_user_gateway, close_remote=terminate_remote_sessions),
    )
    set_post_published_handler(record_post_published)
    set_media_reference_readers(
        MediaReferenceReaders(
            active_identity=active_identity_reference,
            confirmed_paths=confirmed_media_reference_paths,
            identity_is_current=visual_identity_is_current,
        ),
    )
    configure_visual_hooks(
        planning_reader=visual_planning_context,
        ready_handler=schedule_visual_selection,
        preparation_handler=schedule_base_motion_preparation,
        recovery_handler=schedule_visual_selection,
        scene_exists=scene_exists,
    )
    set_appearance_readiness_reader(appearance_readiness)
    set_initial_scene_ensurer(schedule_initial_scene)
