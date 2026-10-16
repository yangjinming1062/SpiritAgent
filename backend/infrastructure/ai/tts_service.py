"""TTS 供应商链调用：REST 合成端点共用的服务层。"""

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.ai.llm_client import MissingLlmConfigError, build_provider, resolve_provider_chain
from infrastructure.ai.llm_fallback import execute_with_fallback
from infrastructure.ai.models import ModelModality
from infrastructure.ai.providers.base import ProviderConfig, ServiceType, TTSProvider, TTSResult, VoiceReferenceAudio
from infrastructure.ai.providers.registry import try_resolve
from infrastructure.ai.speech_contracts import SpeechStyle
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.voice_catalog import pick_voice_id, voices_for_provider
from infrastructure.database import SESSION_LOCAL


def _route_selected_voice(
    voice: str,
    chain: list[ProviderConfig],
    language: str,
    *,
    provider_name: str = "",
    model: str = "",
    model_card_id: str = "",
    design_prompt: str = "",
    reference_audio: VoiceReferenceAudio | None = None,
) -> tuple[str, list[ProviderConfig], str]:
    """解析供应商命名空间中的音色，并收敛为当前语言可用的合成链。"""
    if provider_name or model or model_card_id or design_prompt or reference_audio is not None:
        if not provider_name or not model or not model_card_id:
            raise ValueError("Custom voice requires its configured provider, model and model card")
        selected = next(
            (
                config
                for config in chain
                if config.provider_name == provider_name
                and config.model == model
                and str(config.model_card_id) == model_card_id
            ),
            None,
        )
        if selected is None:
            raise ValueError("Selected voice TTS configuration is no longer available")
        adapter = build_provider(selected, TTSProvider)
        if reference_audio is not None:
            if design_prompt:
                raise ValueError("Voice design and reference audio cannot be combined")
            if selected.model != adapter.VOICE_CLONE_MODEL or ModelModality.audio not in selected.input_modalities:
                raise ValueError("Selected TTS configuration does not support voice cloning")
        elif design_prompt.strip():
            if adapter.VOICE_DESIGN_GUIDE is None or (
                adapter.VOICE_DESIGN_MODEL is not None and selected.model != adapter.VOICE_DESIGN_MODEL
            ):
                raise ValueError("Selected TTS configuration does not support voice design")
        else:
            raise ValueError("Custom voice requires its design prompt or reference audio")
        return voice, [selected], provider_name
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
    if not separator:
        return "", language_chain, ""
    selected = next((config for config in chain if config.provider_name == provider_name), None)
    if selected is None:
        return "", language_chain, ""
    catalog_voice = next((entry for entry in voices_for_provider(provider_name) if entry.id == voice_id), None)
    if catalog_voice is None:
        return "", language_chain, ""
    if language in {"zh", "en"} and catalog_voice.language not in {language, "multi"}:
        return "", language_chain, ""
    return voice_id, [selected, *(config for config in language_chain if config is not selected)], provider_name


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
    provider_name: str = "",
    model: str = "",
    model_card_id: str = "",
    design_prompt: str = "",
    reference_audio: VoiceReferenceAudio | None = None,
) -> tuple[ProviderConfig | None, str]:
    chain = await resolve_provider_chain(db, user_id, ServiceType.tts, task=AiTaskKey.companion_voice)
    voice_id, chain, selected_provider = _route_selected_voice(
        voice,
        chain,
        language,
        provider_name=provider_name,
        model=model,
        model_card_id=model_card_id,
        design_prompt=design_prompt,
        reference_audio=reference_audio,
    )
    if not provider_name:
        chain = _builtin_voice_chain(chain)
    if not chain or chain[0].provider_name not in {"mimo", "minimax"}:
        return None, ""
    config = chain[0]
    if provider_name:
        return config, voice
    selected_voice = pick_voice_id(
        voice_id if config.provider_name == selected_provider else "",
        config.provider_name,
        language,
    )
    return config, f"{config.provider_name}:{selected_voice}"


async def synthesize_speech(
    user_id: int,
    text: str,
    voice: str = "",
    language: str = "",
    speech_style: SpeechStyle | None = None,
    *,
    task: AiTaskKey,
    preserve_performance: bool = False,
    reference_audio: VoiceReferenceAudio | None = None,
    provider_name: str = "",
    model: str = "",
    model_card_id: str = "",
    design_prompt: str = "",
) -> TTSResult:
    """走供应商链合成整段语音；链解析为空抛 MissingLlmConfigError。返回体含实际使用的音色 id。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, ServiceType.tts, task=task)
    if not chain:
        if preserve_performance:
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
    voice, chain, selected_provider = _route_selected_voice(
        voice,
        chain,
        language,
        provider_name=provider_name,
        model=model,
        model_card_id=model_card_id,
        design_prompt=design_prompt,
        reference_audio=reference_audio,
    )
    if not provider_name:
        chain = _builtin_voice_chain(chain)
    if not chain:
        raise MissingLlmConfigError(f"no TTS provider supports language {language!r}")
    if preserve_performance and speech_style is not None:
        config = chain[0]
        if (
            config.provider_name != speech_style.provider
            or config.model != speech_style.model
            or selected_provider != speech_style.provider
        ):
            raise ValueError("Voice message TTS configuration is no longer available")
        chain = [config]

    async def synthesize(provider: TTSProvider) -> TTSResult:
        if reference_audio is not None:
            return await provider.synthesize_with_reference(
                text,
                reference_audio=reference_audio,
                speech_style=speech_style,
            )
        if design_prompt:
            return await provider.synthesize_designed(
                text,
                prompt=design_prompt,
                voice=voice,
                speech_style=speech_style,
            )
        selected_voice = (
            voice
            if provider.provider_name == selected_provider
            else pick_voice_id(voice, provider.provider_name, language)
        )
        return await provider.synthesize(
            text,
            speech_style=speech_style,
            voice=selected_voice,
        )

    return await execute_with_fallback(
        chain,
        TTSProvider,
        synthesize,
        user_id=user_id,
    )
