from components import AiFeature
from modules.conversation import Conversation

from .presets import COMPANION_PRESET_ID


def conversation_ai_feature(conv: Conversation, *, proactive: bool = False) -> AiFeature:
    if conv.parent_id is not None:
        return AiFeature.delegated_task
    if conv.is_automation:
        return AiFeature.scheduled_task
    if proactive:
        return AiFeature.proactive_companion
    return AiFeature.companion_chat if conv.system_preset_id == COMPANION_PRESET_ID else AiFeature.work_chat
