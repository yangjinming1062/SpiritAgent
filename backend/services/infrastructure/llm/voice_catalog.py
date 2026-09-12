from modules.companion import VoiceEntry

from .providers import ServiceType, TTSProvider, try_resolve


def _provider_class(provider_name: str) -> type[TTSProvider] | None:
    if not provider_name:
        return None
    return try_resolve(ServiceType.tts, provider_name)


def voices_for_provider(provider_name: str) -> list[VoiceEntry]:
    cls = _provider_class(provider_name)
    if cls is None:
        return []
    return [VoiceEntry(provider=provider_name, **v) for v in cls.VOICE_CATALOG]


def default_voice_id(provider_name: str, language: str = "") -> str:
    # 优先选中性音色 —— 否则未匹配偏好时会锁到第一个带性别的条目。
    voices = voices_for_provider(provider_name)
    if language in {"zh", "en"}:
        voices = [voice for voice in voices if voice.language in {language, "multi"}]
    for v in voices:
        if v.gender == "neutral":
            return v.id
    return voices[0].id if voices else ""


def pick_voice_id(voice: str, provider_name: str, language: str = "") -> str:
    # voice id 是供应商私有的：仅在供应商自有 catalog 中才透传；否则回退到该供应商默认，避免外部 id 进入 ``synthesize()`` 后 400。
    voices = voices_for_provider(provider_name)
    if language in {"zh", "en"}:
        voices = [entry for entry in voices if entry.language in {language, "multi"}]
    if voice and voice in (entry.id for entry in voices):
        return voice
    return default_voice_id(provider_name, language)
