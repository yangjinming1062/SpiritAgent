from components import AiTaskKey
from modules.conversation import Conversation

from .presets import COMPANION_PRESET_ID


def conversation_ai_task(conv: Conversation, *, proactive: bool = False) -> AiTaskKey:
    """会话所属的模型任务：子会话沿用父会话的预设与自动化标志，任务与父会话一致（环节随任务链继承）。"""
    if conv.is_automation:
        return AiTaskKey.scheduled_task
    if proactive:
        return AiTaskKey.proactive
    return AiTaskKey.companion_chat if conv.system_preset_id == COMPANION_PRESET_ID else AiTaskKey.work_chat
