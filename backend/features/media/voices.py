"""音色样本、试听候选和确认操作；供应商等待在事务外。"""

import asyncio
import base64
from datetime import timedelta
from uuid import uuid4

from infrastructure.ai.llm_client import MissingLlmConfigError, resolve_provider_chain
from infrastructure.ai.llm_fallback import execute_with_fallback
from infrastructure.ai.providers.base import (
    ProviderConfig,
    TTSProvider,
    TTSResult,
    VoiceDesignResult,
    VoiceReferenceAudio,
)
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import client_asset_url, save_companion_asset_async, unlink_companion_asset
from infrastructure.assets.write_protection import protect_asset_write, user_asset_lock
from infrastructure.constants import (
    MAX_VOICE_DESIGN_PROMPT_CHARS,
    STT_MAX_AUDIO_BYTES,
    TTS_MAX_TEXT_CHARS,
    VOICE_REFERENCE_MAX_BASE64_BYTES,
)
from infrastructure.database import session_scope
from infrastructure.request_errors import RequestError
from infrastructure.text import ensure_utc, utc_now
from infrastructure.uploads import UploadSource, read_capped_upload
from infrastructure.video_processing.audio import audio_bytes_info, voice_sample_wav_bytes
from infrastructure.video_processing.ffmpeg import VideoProcessError
from runtime.events.emit import emit_ws_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.lifecycle import enqueue_asset_cleanup
from features.configuration.settings.values import get_user_setting, lock_user_setting, put_user_settings

from .speech import _reference_bytes
from .voice_contracts import VoiceConfirmResponse, VoicePreviewResponse, VoiceSampleResponse
from .voice_models import CustomVoice
from .voice_queries import custom_voice_reference

_SAMPLE_TTL = timedelta(hours=24)


def validate_voice_prompt(prompt: str) -> str:
    prompt = prompt.strip()
    if not prompt or len(prompt) > MAX_VOICE_DESIGN_PROMPT_CHARS or "\x00" in prompt:
        raise ValueError(f"声音描述须为 1–{MAX_VOICE_DESIGN_PROMPT_CHARS} 个字符")
    return prompt


def _preview_text(text: str) -> str:
    text = text.strip()
    if not text or len(text) > TTS_MAX_TEXT_CHARS:
        raise ValueError("请提供有效的试听文字")
    return text


async def design_configuration(user_id: int, provider_name: str = "") -> ProviderConfig:
    async with session_scope() as db:
        chain = await resolve_provider_chain(db, user_id, "tts", task=AiTaskKey.voice_design)
    config = next((item for item in chain if not provider_name or item.provider_name == provider_name), None)
    if config is None:
        raise MissingLlmConfigError("音色设计服务未配置或已不可用")
    return config


async def upload_voice_sample(user_id: int, file: UploadSource) -> VoiceSampleResponse:
    data = await read_capped_upload(
        file,
        STT_MAX_AUDIO_BYTES,
        RequestError(status_code=413, message="声音样本不能超过 24 MiB"),
    )
    if not data:
        raise RequestError(status_code=422, message="音频文件为空，请重新录制或上传")
    try:
        normalized, duration = await asyncio.to_thread(
            voice_sample_wav_bytes,
            data,
            max_duration=60,
            max_bytes=VOICE_REFERENCE_MAX_BASE64_BYTES * 3 // 4,
        )
    except (ValueError, VideoProcessError) as exc:
        raise RequestError(status_code=422, message=str(exc)) from exc
    path = await save_companion_asset_async(
        normalized,
        user_id=user_id,
        label="voice-sample",
        ext="wav",
        directory="voices",
    )
    token = uuid4().hex
    try:
        async with session_scope() as db:
            db.add(
                CustomVoice(
                    user_id=user_id,
                    token=token,
                    kind="sample",
                    reference_path=path,
                    duration=duration,
                    expires_at=utc_now() + _SAMPLE_TTL,
                ),
            )
            await db.commit()
    except BaseException:
        await asyncio.to_thread(unlink_companion_asset, path)
        raise
    return VoiceSampleResponse(sample_id=token, url=client_asset_url(path), duration=duration)


async def _expected_state(user_id: int) -> tuple[int, str]:
    async with session_scope() as db:
        value = await db.scalar(select(func.max(CustomVoice.version)).where(CustomVoice.user_id == user_id))
        voice = await get_user_setting(db, user_id, "companion.voice_id")
    return value or 0, voice or ""


async def _save_preview(
    user_id: int,
    kind: str,
    config: ProviderConfig,
    result: TTSResult | VoiceDesignResult,
    expected_version: int,
    expected_voice_id: str,
    *,
    prompt: str = "",
    reference_path: str | None = None,
    duration: float = 0,
) -> VoicePreviewResponse:
    audio = result.trial_audio if isinstance(result, VoiceDesignResult) else result.audio
    mime = result.trial_audio_mime if isinstance(result, VoiceDesignResult) else result.mime
    native_voice = result.voice_id if isinstance(result, VoiceDesignResult) else ""
    _, extension = await asyncio.to_thread(audio_bytes_info, audio)
    path = await save_companion_asset_async(
        audio,
        user_id=user_id,
        label="voice-preview",
        ext=extension,
        directory="voices",
    )
    token = uuid4().hex
    try:
        async with session_scope() as db:
            db.add(
                CustomVoice(
                    user_id=user_id,
                    token=token,
                    kind=kind,
                    provider=config.provider_name,
                    model=config.model,
                    model_card_id=str(config.model_card_id),
                    provider_voice_id=native_voice,
                    prompt=prompt,
                    reference_path=reference_path,
                    preview_path=path,
                    duration=duration,
                    expected_version=expected_version,
                    expected_voice_id=expected_voice_id,
                    expires_at=utc_now() + _SAMPLE_TTL,
                ),
            )
            await db.commit()
    except BaseException:
        await asyncio.to_thread(unlink_companion_asset, path)
        raise
    return VoicePreviewResponse(
        candidate_id=token,
        voice_id=custom_voice_reference(config.provider_name, token),
        provider=config.provider_name,
        trial_audio_base64=base64.b64encode(audio).decode("ascii"),
        trial_audio_mime=mime,
        expected_version=expected_version,
        kind=kind,
        prompt=prompt,
    )


async def design_voice(
    user_id: int,
    prompt: str,
    *,
    preview_text: str,
    provider_name: str = "",
) -> VoicePreviewResponse:
    prompt = validate_voice_prompt(prompt)
    config = await design_configuration(user_id, provider_name)
    version, selected_voice = await _expected_state(user_id)
    preview_text = _preview_text(preview_text)
    result = await execute_with_fallback(
        [config],
        TTSProvider,
        lambda provider: provider.design_voice(prompt, preview_text=preview_text),
        user_id=user_id,
    )
    return await _save_preview(user_id, "design", config, result, version, selected_voice, prompt=prompt)


async def clone_voice(user_id: int, sample_id: str, *, preview_text: str) -> VoicePreviewResponse:
    async with session_scope() as db:
        chain = await resolve_provider_chain(db, user_id, "tts", task=AiTaskKey.voice_clone)
    if not chain:
        raise MissingLlmConfigError("声音克隆服务未配置或已不可用")
    config = chain[0]
    async with user_asset_lock(user_id):
        async with session_scope() as db:
            row = await db.scalar(
                select(CustomVoice).where(
                    CustomVoice.user_id == user_id,
                    CustomVoice.token == sample_id,
                    CustomVoice.kind == "sample",
                ),
            )
            if (
                row is None
                or not row.reference_path
                or row.expires_at is None
                or ensure_utc(row.expires_at) <= utc_now()
            ):
                raise ValueError("声音样本不存在或已过期，请重新录制或上传")
            path, duration = row.reference_path, row.duration
        protect_asset_write(path)
        try:
            reference: VoiceReferenceAudio = await asyncio.to_thread(_reference_bytes, user_id, path)
        except OSError as exc:
            raise ValueError("声音样本不可读取，请重新录制或上传") from exc
    version, selected_voice = await _expected_state(user_id)
    preview_text = _preview_text(preview_text)
    result = await execute_with_fallback(
        [config],
        TTSProvider,
        lambda provider: provider.synthesize_with_reference(preview_text, reference_audio=reference, speech_style=None),
        user_id=user_id,
    )
    return await _save_preview(
        user_id,
        "clone",
        config,
        result,
        version,
        selected_voice,
        reference_path=path,
        duration=duration,
    )


async def confirm_voice(user_id: int, candidate_id: str, expected_version: int) -> VoiceConfirmResponse:
    async with user_asset_lock(user_id), session_scope() as db:
        candidate = await db.scalar(
            select(CustomVoice)
            .where(CustomVoice.user_id == user_id, CustomVoice.token == candidate_id)
            .with_for_update(),
        )
        if candidate is None or candidate.kind not in {"design", "clone"}:
            raise ValueError("试听方案不存在，请重新生成")
        current_voice = await lock_user_setting(db, user_id, "companion.voice_id", "")
        for task in (AiTaskKey.companion_voice, AiTaskKey.speech_preview):
            chain = await resolve_provider_chain(db, user_id, "tts", task=task)
            if not any(
                config.provider_name == candidate.provider
                and config.model == candidate.model
                and str(config.model_card_id) == candidate.model_card_id
                and (candidate.kind != "clone" or "audio" in config.input_modalities)
                for config in chain
            ):
                raise ValueError("该声音的模型已不可用，请配置伙伴语音和朗读模型后重试")
        if candidate.reference_path:
            await asyncio.to_thread(_reference_bytes, user_id, candidate.reference_path)
        if candidate.is_current:
            if current_voice != custom_voice_reference(candidate.provider, candidate.token):
                raise ValueError("当前使用的声音已改变，请刷新后重新选择")
            return VoiceConfirmResponse(
                voice_id=custom_voice_reference(candidate.provider, candidate.token),
                version=candidate.version,
            )
        if candidate.version or candidate.expires_at is None or ensure_utc(candidate.expires_at) <= utc_now():
            raise ValueError("试听方案已过期或已被替换，请重新生成")
        previous = await db.scalar(
            select(CustomVoice)
            .where(CustomVoice.user_id == user_id, CustomVoice.kind == candidate.kind, CustomVoice.is_current.is_(True))
            .with_for_update(),
        )
        current_version = (
            await db.scalar(select(func.max(CustomVoice.version)).where(CustomVoice.user_id == user_id)) or 0
        )
        if (
            current_version != expected_version
            or candidate.expected_version != expected_version
            or candidate.expected_voice_id != current_voice
        ):
            raise ValueError("声音方案已在其他窗口更新，请刷新后重试")
        if previous:
            previous.is_current = False
            await enqueue_asset_cleanup(
                db,
                user_id,
                [path for path in (previous.reference_path, previous.preview_path) if path],
            )
            await db.flush()
        candidate.is_current = True
        candidate.version = current_version + 1
        candidate.expires_at = None
        voice_id = custom_voice_reference(candidate.provider, candidate.token)
        await put_user_settings(db, user_id, {"companion.voice_id": voice_id})
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.voice.updated",
            payload={"voice_id": voice_id, "version": candidate.version},
        )
        await db.commit()
        return VoiceConfirmResponse(voice_id=voice_id, version=candidate.version)


async def read_current_voice(db: AsyncSession, user_id: int) -> str:
    value = await get_user_setting(db, user_id, "companion.voice_id")
    return str(value or "")


async def write_current_voice(db: AsyncSession, user_id: int, voice_id: str) -> str:
    await put_user_settings(db, user_id, {"companion.voice_id": voice_id})
    await db.commit()
    return voice_id
