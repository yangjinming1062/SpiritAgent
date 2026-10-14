"""TTS 供应商链调用：REST 合成端点共用的服务层。"""

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.ai.llm_client import MissingLlmConfigError, build_provider, resolve_provider_chain
from infrastructure.ai.llm_fallback import execute_with_fallback
from infrastructure.ai.providers.base import ProviderConfig, ServiceType, TTSProvider, TTSResult
from infrastructure.ai.speech_contracts import SpeechStyle
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.voice_catalog import pick_voice_id, voices_for_provider
from infrastructure.database import SESSION_LOCAL


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
    if not is_custom_voice and catalog_voice is None:
        return "", language_chain, ""
    if language in {"zh", "en"} and catalog_voice and catalog_voice.language not in {language, "multi"}:
        return "", language_chain, ""
    return voice_id, [selected, *(config for config in language_chain if config is not selected)], provider_name


def _voice_model_chain(chain: list[ProviderConfig], voice_id: str, selected_provider: str) -> list[ProviderConfig]:
    """从已有模型卡片中选择适配音色的配置，不修改卡片的模型。"""
    return [
        config
        for config in chain
        if config.provider_name != selected_provider
        or build_provider(config, TTSProvider).model_for_voice(voice_id) == config.model
    ]


async def resolve_reply_voice(
    db: AsyncSession,
    user_id: int,
    voice: str,
    language: str,
) -> tuple[ProviderConfig | None, str]:
    chain = await resolve_provider_chain(db, user_id, ServiceType.tts, task=AiTaskKey.companion_voice)
    voice_id, chain, selected_provider = _route_selected_voice(voice, chain, language)
    chain = _voice_model_chain(chain, voice_id, selected_provider)
    if not chain or chain[0].provider_name not in {"mimo", "minimax"}:
        return None, ""
    config = chain[0]
    selected_voice = voice_id or pick_voice_id("", config.provider_name, language)
    custom = "custom:" if voice_id and ":custom:" in voice else ""
    return config, f"{config.provider_name}:{custom}{selected_voice}"


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
    voice, chain, selected_provider = _route_selected_voice(voice, chain, language)
    chain = _voice_model_chain(chain, voice, selected_provider)
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
    return await execute_with_fallback(
        chain,
        TTSProvider,
        lambda p: p.synthesize(
            text,
            speech_style=speech_style,
            voice=voice if p.provider_name == selected_provider else pick_voice_id(voice, p.provider_name, language),
        ),
        user_id=user_id,
    )
