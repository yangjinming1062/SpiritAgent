"""TTS 供应商链调用：REST 合成端点共用的服务层。"""

from collections.abc import AsyncIterator
from dataclasses import replace

from components import SESSION_LOCAL
from modules.media import SpeechStyle
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_client import MissingLlmConfigError, resolve_provider_chain
from .llm_fallback import execute_stream_with_fallback, execute_with_fallback
from .providers.base import AudioChunk, ProviderConfig, TTSResult
from .voice_catalog import pick_voice_id, voices_for_provider


def _route_selected_voice(
    voice: str,
    chain: list[ProviderConfig],
    language: str,
) -> tuple[str, list[ProviderConfig], str]:
    """解析供应商命名空间中的音色，并收敛为当前语言可用的合成链。"""
    provider_name, separator, voice_id = voice.partition(":")
    is_custom_voice = voice_id.startswith("custom:")
    if is_custom_voice:
        voice_id = voice_id.removeprefix("custom:")
    language_chain = chain
    if language in {"zh", "en"}:
        matching_chain = [
            config
            for config in chain
            if any(entry.language in {language, "multi"} for entry in voices_for_provider(config.provider_name))
        ]
        language_chain = matching_chain
    if not separator:
        return "", language_chain, ""
    selected = next((config for config in chain if config.provider_name == provider_name), None)
    if selected is None:
        return "", language_chain, ""
    catalog_voice = next((entry for entry in voices_for_provider(provider_name) if entry.id == voice_id), None)
    if not is_custom_voice and catalog_voice is None:
        return "", language_chain, ""
    if language in {"zh", "en"} and catalog_voice and catalog_voice.language not in {language, "multi"}:
        return "", language_chain, ""
    return voice_id, [selected, *(config for config in language_chain if config is not selected)], provider_name


async def resolve_speech_style_config(
    db: AsyncSession,
    user_id: int,
    voice: str,
    language: str,
) -> ProviderConfig | None:
    chain = await resolve_provider_chain(db, user_id, "tts")
    voice_id, chain, _ = _route_selected_voice(voice, chain, language)
    if not chain or chain[0].provider_name not in {"mimo", "minimax"}:
        return None
    config = chain[0]
    if config.provider_name == "mimo" and voice_id.startswith("mimo_voicedesign:"):
        return replace(config, model="mimo-v2.5-tts-voicedesign")
    return config


async def synthesize_speech(
    user_id: int,
    text: str,
    voice: str = "",
    language: str = "",
    speech_style: SpeechStyle | None = None,
) -> TTSResult:
    """走供应商链合成整段语音；链解析为空抛 MissingLlmConfigError。返回体含实际使用的音色 id。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "tts")
    if not chain:
        raise MissingLlmConfigError()
    voice, chain, selected_provider = _route_selected_voice(voice, chain, language)
    if not chain:
        raise MissingLlmConfigError(f"no TTS provider supports language {language!r}")
    return await execute_with_fallback(
        db=None,
        user_id=user_id,
        service_type="tts",
        call_fn=lambda p: p.synthesize(
            text,
            speech_style=speech_style,
            voice=voice if p.provider_name == selected_provider else pick_voice_id(voice, p.provider_name, language),
        ),
        _chain=chain,
    )


async def synthesize_speech_stream(
    user_id: int,
    text: str,
    voice: str = "",
    language: str = "",
    speech_style: SpeechStyle | None = None,
) -> AsyncIterator[AudioChunk]:
    """走供应商链流式合成；回退仅发生在首块之前（execute_stream_with_fallback 语义）。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "tts")
    if not chain:
        raise MissingLlmConfigError()
    voice, chain, selected_provider = _route_selected_voice(voice, chain, language)
    if not chain:
        raise MissingLlmConfigError(f"no TTS provider supports language {language!r}")
    async for chunk in execute_stream_with_fallback(
        db=None,
        user_id=user_id,
        service_type="tts",
        open_fn=lambda p: p.synthesize_stream(
            text,
            speech_style=speech_style,
            voice=voice if p.provider_name == selected_provider else pick_voice_id(voice, p.provider_name, language),
        ),
        _chain=chain,
    ):
        yield chunk
