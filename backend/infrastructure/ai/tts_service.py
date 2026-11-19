"""TTS 供应商链调用：REST 合成端点共用的服务层。"""

from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.ai.llm_client import MissingLlmConfigError, build_provider, resolve_provider_chain
from infrastructure.ai.llm_fallback import execute_with_fallback
from infrastructure.ai.models import ModelModality
from infrastructure.ai.providers.base import (
    ProviderCapabilityError,
    ProviderConfig,
    ServiceType,
    TTSProvider,
    TTSResult,
    TTSStreamChunk,
    VoiceReferenceAudio,
)
from infrastructure.ai.providers.registry import provider_supports_streaming, try_resolve
from infrastructure.ai.speech_contracts import SpeechStyle
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.voice_catalog import pick_voice_id, voices_for_provider
from infrastructure.database import SESSION_LOCAL

PREFERRED_VOICE_PROVIDERS: tuple[str, ...] = ("mimo", "minimax")


@dataclass(frozen=True)
class CustomVoiceInput:
    provider: str
    model: str
    model_card_id: str
    voice_id: str
    design_prompt: str
    reference_audio: VoiceReferenceAudio | None


def _route_selected_voice(
    voice: str, chain: list[ProviderConfig], language: str, custom: CustomVoiceInput | None
) -> tuple[str, list[ProviderConfig], str]:
    """固定自定义音色的模型卡；内置音色按语言、已选供应商和可用模型排序。"""
    if custom is not None:
        selected = next(
            (
                config
                for config in chain
                if config.provider_name == custom.provider
                and config.model == custom.model
                and str(config.model_card_id) == custom.model_card_id
            ),
            None,
        )
        if selected is None:
            raise ValueError("Selected voice TTS configuration is no longer available")
        adapter = build_provider(selected, TTSProvider)
        if custom.reference_audio is not None:
            if custom.design_prompt:
                raise ValueError("Voice design and reference audio cannot be combined")
            if selected.model != adapter.VOICE_CLONE_MODEL or ModelModality.audio not in selected.input_modalities:
                raise ValueError("Selected TTS configuration does not support voice cloning")
        elif custom.design_prompt.strip():
            if adapter.VOICE_DESIGN_GUIDE is None or (
                adapter.VOICE_DESIGN_MODEL is not None and selected.model != adapter.VOICE_DESIGN_MODEL
            ):
                raise ValueError("Selected TTS configuration does not support voice design")
        else:
            raise ValueError("Custom voice requires its design prompt or reference audio")
        return custom.voice_id, [selected], custom.provider
    provider_name, separator, voice_id = voice.partition(":")
    if voice_id.startswith("custom:"):
        raise ValueError("Custom voice must be resolved before synthesis")
    language_chain = chain
    if language in {"zh", "en"}:
        language_chain = [
            config
            for config in chain
            if any(entry.language in {language, "multi"} for entry in voices_for_provider(config.provider_name))
        ]
    if separator:
        selected = next((config for config in chain if config.provider_name == provider_name), None)
        catalog_voice = next((entry for entry in voices_for_provider(provider_name) if entry.id == voice_id), None)
        if (
            selected
            and catalog_voice
            and (language not in {"zh", "en"} or catalog_voice.language in {language, "multi"})
        ):
            configs = [selected, *(config for config in language_chain if config is not selected)]
            return voice_id, _builtin_voice_chain(configs), provider_name

    rank = {name: index for index, name in enumerate(PREFERRED_VOICE_PROVIDERS)}
    return (
        "",
        sorted(_builtin_voice_chain(language_chain), key=lambda config: rank.get(config.provider_name, len(rank))),
        "",
    )


def _builtin_voice_chain(chain: list[ProviderConfig]) -> list[ProviderConfig]:
    return [
        config
        for config in chain
        if (provider := try_resolve(ServiceType.tts, config.provider_name)) is not None
        and issubclass(provider, TTSProvider)
        and (provider.BUILTIN_VOICE_MODEL is None or config.model == provider.BUILTIN_VOICE_MODEL)
    ]


async def resolve_reply_voice(
    db: AsyncSession,
    user_id: int,
    voice: str,
    language: str,
    *,
    prefer_streaming: bool = False,
    custom: CustomVoiceInput | None = None,
) -> tuple[ProviderConfig | None, str]:
    chain = await resolve_provider_chain(db, user_id, ServiceType.tts, task=AiTaskKey.companion_voice)
    voice_id, chain, selected_provider = _route_selected_voice(voice, chain, language, custom)
    if prefer_streaming and not selected_provider:
        streaming = [
            config
            for config in chain
            if config.provider_name in PREFERRED_VOICE_PROVIDERS
            and provider_supports_streaming(ServiceType.tts, config.provider_name, config)
        ]
        chain = streaming or chain
    if not chain or chain[0].provider_name not in PREFERRED_VOICE_PROVIDERS:
        return None, ""
    config = chain[0]
    if custom is not None:
        return config, voice
    selected_voice = pick_voice_id(
        voice_id if config.provider_name == selected_provider else "", config.provider_name, language
    )
    return config, f"{config.provider_name}:{selected_voice}"


async def _synthesis_route(
    user_id: int,
    task: AiTaskKey,
    voice: str,
    language: str,
    speech_style: SpeechStyle | None,
    custom: CustomVoiceInput | None,
    *,
    preserve_performance: bool,
    streaming: bool,
) -> tuple[str, list[ProviderConfig], str]:
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, ServiceType.tts, task=task)
    if not chain:
        if preserve_performance and not streaming:
            raise ValueError("Voice message TTS configuration is no longer available")
        raise MissingLlmConfigError()
    if preserve_performance:
        if speech_style is None:
            raise ValueError("Voice message requires speech performance")
        chain = [
            config
            for config in chain
            if config.provider_name == speech_style.provider and config.model == speech_style.model
        ]
        if not chain:
            raise ValueError("Voice message TTS configuration is no longer available")

    voice, chain, selected_provider = _route_selected_voice(voice, chain, language, custom)
    if not chain and not streaming:
        raise MissingLlmConfigError(f"no TTS provider supports language {language!r}")
    if preserve_performance:
        if not chain or chain[0].provider_name != selected_provider:
            raise ValueError("Voice message TTS configuration is no longer available")
        chain = chain[:1]
    if streaming:
        chain = [
            config for config in chain if provider_supports_streaming(ServiceType.tts, config.provider_name, config)
        ]
        if not chain:
            raise ProviderCapabilityError("configured TTS providers do not support streaming")
    return voice, chain, selected_provider


async def synthesize_speech(
    user_id: int,
    text: str,
    voice: str = "",
    language: str = "",
    speech_style: SpeechStyle | None = None,
    *,
    task: AiTaskKey,
    preserve_performance: bool = False,
    custom: CustomVoiceInput | None = None,
) -> TTSResult:
    """走供应商链合成整段语音；链解析为空抛 MissingLlmConfigError。返回体含实际使用的音色 id。"""
    voice, chain, selected_provider = await _synthesis_route(
        user_id,
        task,
        voice,
        language,
        speech_style,
        custom,
        preserve_performance=preserve_performance,
        streaming=False,
    )

    async def synthesize(provider: TTSProvider) -> TTSResult:
        if custom is not None:
            if custom.reference_audio is not None:
                return await provider.synthesize_with_reference(
                    text, reference_audio=custom.reference_audio, speech_style=speech_style
                )
            return await provider.synthesize_designed(
                text, prompt=custom.design_prompt, voice=voice, speech_style=speech_style
            )
        selected_voice = (
            voice
            if provider.provider_name == selected_provider
            else pick_voice_id(voice, provider.provider_name, language)
        )
        return await provider.synthesize(text, speech_style=speech_style, voice=selected_voice)

    return await execute_with_fallback(chain, TTSProvider, synthesize, user_id=user_id)


async def stream_synthesize_speech(
    user_id: int,
    text: str,
    voice: str = "",
    language: str = "",
    speech_style: SpeechStyle | None = None,
    *,
    task: AiTaskKey,
    preserve_performance: bool = False,
    custom: CustomVoiceInput | None = None,
) -> AsyncGenerator[TTSStreamChunk]:
    """流式 TTS 入口；只选择明确声明增量能力的配置，不把整段接口伪装为流式。"""
    voice, chain, selected_provider = await _synthesis_route(
        user_id,
        task,
        voice,
        language,
        speech_style,
        custom,
        preserve_performance=preserve_performance,
        streaming=True,
    )

    provider = build_provider(chain[0], TTSProvider)
    selected_voice = (
        voice if provider.provider_name == selected_provider else pick_voice_id(voice, provider.provider_name, language)
    )
    chunks = provider.synthesize_stream(
        text,
        voice=selected_voice,
        speech_style=speech_style,
        design_prompt=custom.design_prompt if custom else "",
        reference_audio=custom.reference_audio if custom else None,
    )
    async with aclosing(chunks):
        async for chunk in chunks:
            yield chunk
