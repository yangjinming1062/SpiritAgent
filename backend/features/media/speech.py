"""所有语音消费方共用的音色解析；供应商只消费校验后的声音输入。"""

import asyncio
from dataclasses import replace

from infrastructure.ai.providers.base import ProviderConfig, TTSResult, VoiceReferenceAudio
from infrastructure.ai.speech_contracts import SpeechStyle
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.tts_service import resolve_reply_voice as resolve_provider_voice
from infrastructure.ai.tts_service import synthesize_speech as synthesize_provider_speech
from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.assets.write_protection import protect_asset_write, user_asset_lock
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from .voice_contracts import CustomVoiceSnapshot
from .voice_queries import resolve_custom_voice

logger = get_logger(__name__)


def _reference_bytes(user_id: int, path: str) -> VoiceReferenceAudio:
    parsed = parse_companion_asset_path(path)
    if parsed is None or parsed[0] != user_id:
        raise ValueError("声音样本不属于当前账户")
    resolved = resolve_asset_reference(path)
    if resolved is None or resolved[1] != "audio/wav":
        raise ValueError("声音样本不可读取，请重新录制或上传")
    return VoiceReferenceAudio(audio=resolved[0].read_bytes(), mime="audio/wav")


async def _protect_reference(user_id: int, snapshot: CustomVoiceSnapshot) -> VoiceReferenceAudio | None:
    if snapshot.reference_path is None:
        return None
    async with user_asset_lock(user_id):
        protect_asset_write(snapshot.reference_path)
        try:
            return await asyncio.to_thread(_reference_bytes, user_id, snapshot.reference_path)
        except OSError as exc:
            raise ValueError("声音样本已不可读取，请重新录制或上传") from exc


async def resolve_reply_voice(
    db: AsyncSession,
    user_id: int,
    voice: str,
    language: str,
) -> tuple[ProviderConfig | None, str]:
    try:
        snapshot = await resolve_custom_voice(db, user_id, voice)
        if snapshot is None:
            return await resolve_provider_voice(db, user_id, voice, language)
        reference = await _protect_reference(user_id, snapshot)
        return await resolve_provider_voice(
            db,
            user_id,
            voice,
            language,
            provider_name=snapshot.provider,
            model=snapshot.model,
            model_card_id=snapshot.model_card_id,
            design_prompt=snapshot.prompt,
            reference_audio=reference,
        )
    except ValueError:
        logger.warning("Reply voice unavailable; continuing with text", extra={"user_id": user_id}, exc_info=True)
        return None, ""


async def synthesize_speech(
    user_id: int,
    text: str,
    voice: str = "",
    language: str = "",
    speech_style: SpeechStyle | None = None,
    *,
    task: AiTaskKey,
    preserve_performance: bool = False,
) -> TTSResult:
    async with session_scope() as db:
        snapshot = await resolve_custom_voice(db, user_id, voice)
    if snapshot is None:
        return await synthesize_provider_speech(
            user_id,
            text,
            voice,
            language,
            speech_style,
            task=task,
            preserve_performance=preserve_performance,
        )
    reference = await _protect_reference(user_id, snapshot)
    result = await synthesize_provider_speech(
        user_id,
        text,
        snapshot.provider_voice_id,
        language,
        speech_style,
        task=task,
        preserve_performance=preserve_performance,
        reference_audio=reference,
        provider_name=snapshot.provider,
        model=snapshot.model,
        model_card_id=snapshot.model_card_id,
        design_prompt=snapshot.prompt,
    )
    return replace(result, voice=voice)
