"""将伙伴资料的只读快照装配为音色设计请求。"""

import json

from features.companion.definition import parse_persona_definition
from features.companion.visual_state import persona_snapshot
from features.media.voice_contracts import VoicePromptResponse
from features.media.voices import design_configuration, validate_voice_prompt
from infrastructure.ai.llm_client import build_provider
from infrastructure.ai.prompt_engineer import chat
from infrastructure.ai.providers.base import TTSProvider
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.constants import MAX_VOICE_DESIGN_PROMPT_CHARS
from infrastructure.database import session_scope

_VOICE_DESCRIPTION_TASK = """根据角色资料写出日常说话的音色描述，优先满足声音偏好。
{guide}

修改时以本次意见为准，只调整或补充意见涉及的声音特征，其余沿用当前描述。
只输出音色描述正文，最多{limit}个字符。"""


async def generate_voice_prompt(
    user_id: int,
    current_prompt: str = "",
    feedback: str = "",
    preference: str = "",
) -> VoicePromptResponse:
    current_prompt, feedback, preference = current_prompt.strip(), feedback.strip(), preference.strip()
    if len(current_prompt) > MAX_VOICE_DESIGN_PROMPT_CHARS or max(len(feedback), len(preference)) > 2000:
        raise ValueError("声音描述或修改意见过长")
    if feedback and not current_prompt:
        raise ValueError("请先提供要调整的声音描述")
    config = await design_configuration(user_id)
    guide = build_provider(config, TTSProvider).VOICE_DESIGN_GUIDE
    if not guide:
        raise ValueError("音色设计写法未配置")
    async with session_scope() as db:
        persona = await persona_snapshot(db, user_id)
    if persona is None:
        raise ValueError("请先填写伙伴资料")
    definition = parse_persona_definition(persona.definition_json)
    fields = {
        "name": "名字",
        "biological_type": "物种",
        "gender": "性别",
        "personality": "性格",
        "speaking_style": "说话风格",
    }
    payload = {
        "角色资料": {label: definition[key] for key, label in fields.items() if definition.get(key)},
        "声音偏好": preference,
        "当前声音描述": current_prompt,
        "本次修改意见": feedback,
    }
    response = await chat(
        user_id,
        _VOICE_DESCRIPTION_TASK.format(limit=MAX_VOICE_DESIGN_PROMPT_CHARS, guide=guide),
        json.dumps({key: value for key, value in payload.items() if value}, ensure_ascii=False),
        task=AiTaskKey.voice_design,
    )
    prompt = validate_voice_prompt(response)
    return VoicePromptResponse(prompt=prompt, provider=config.provider_name)
