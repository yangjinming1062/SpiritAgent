from modules.companion import VoiceEntry

from .providers import ServiceType, TTSProvider, try_resolve


def voices_for_provider(provider_name: str) -> list[VoiceEntry]:
    cls = try_resolve(ServiceType.tts, provider_name)
    if cls is None or not issubclass(cls, TTSProvider):
        return []
    return [VoiceEntry(provider=provider_name, **v) for v in cls.VOICE_CATALOG]


def pick_voice_id(voice: str, provider_name: str, language: str = "") -> str:
    """voice id 是供应商私有的：仅在供应商自有目录（按语言过滤）中才透传，否则取该供应商默认音色，避免外部 id 触发 400。"""
    voices = voices_for_provider(provider_name)
    if language in {"zh", "en"}:
        voices = [entry for entry in voices if entry.language in {language, "multi"}]
    if voice and any(entry.id == voice for entry in voices):
        return voice
    # 优先中性音色，否则未匹配偏好时会锁到第一个带性别的条目。
    default = next((entry for entry in voices if entry.gender == "neutral"), voices[0] if voices else None)
    return default.id if default else ""
