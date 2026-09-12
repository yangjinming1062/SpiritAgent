from modules.companion import VoiceEntry, VoiceMatchResponse, VoicesListResponse
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.llm import (
    ServiceType,
    VoiceDesignResult,
    resolve,
    resolve_provider_chain,
    try_resolve,
    voices_for_provider,
)

_LANG_KEYWORDS: dict[str, list[str]] = {
    "zh": ["中文", "普通话", "国语", "chinese", "mandarin"],
    "en": ["英文", "英语", "english"],
}

# 按整词（不分大小写）匹配的性别关键词，避免子串误判（"male" 命中 "female voice"）
_GENDER_KEYWORDS: dict[str, list[str]] = {
    "female": ["female", "女", "女声", "少女", "御姐", "女神"],
    "male": ["male", "男", "男声", "少年", "正太"],
}


# 稳定排序序位：zh → multi → ∅ → en，桶内保持原始顺序；放模块级避免每次调用重新分配
_LANGUAGE_BUCKET: dict[str, int] = {"zh": 0, "multi": 1, "": 2, "en": 3}

# 由 _LANGUAGE_BUCKET 派生，保证支持集合与排序顺序不会失同步
SUPPORTED_VOICE_LANGUAGES: frozenset[str] = frozenset(_LANGUAGE_BUCKET)


def normalize_voice_language(value: object) -> str | None:
    """REST 与 JSON-RPC 列表接口共用的语言取值归一化规则，非法值统一返回 None。"""
    return value if isinstance(value, str) and value in SUPPORTED_VOICE_LANGUAGES else None


def _sort_voices_by_language(voices: list[VoiceEntry]) -> list[VoiceEntry]:
    return sorted(voices, key=lambda v: _LANGUAGE_BUCKET.get(v.language or "", 4))


async def list_tts_voices(db: AsyncSession, user_id: int, language: str | None = None) -> VoicesListResponse:
    """返回全部已配置 TTS 供应商的音色目录，并按当前界面语言收敛。"""
    chain = await resolve_provider_chain(db, user_id, "tts")
    voices = _sort_voices_by_language(
        [voice for config in chain for voice in voices_for_provider(config.provider_name)],
    )
    if language:
        voices = [v for v in voices if v.language in {language, "multi"}]
    design_cls = try_resolve(ServiceType.tts, chain[0].provider_name) if chain else None
    guide = design_cls.VOICE_DESIGN_GUIDE if design_cls else None
    return VoicesListResponse(
        providers=[config.provider_name for config in chain],
        voices=voices,
        supports_voice_design=guide is not None,
        voice_design_guide=guide or "",
    )


def _score(preference: str, voice: VoiceEntry) -> int:
    p = preference.lower()
    p_tokens = p.split()
    score = 0
    for tag in voice.tags:
        t = tag.lower()
        if t in p:
            score += 2
        elif any(tok and tok in t for tok in p_tokens) or any(
            tok and (tok in t or t in p) for tok in p_tokens if any("一" <= c <= "鿿" for c in tok)
        ):
            score += 1
    if voice.label.lower() in p:
        score += 2
    if voice.language and voice.language != "multi":
        kws = _LANG_KEYWORDS.get(voice.language, [])
        if any(kw.lower() in p for kw in kws):
            score += 2
    # 性别偏好按 token 匹配，避免子串误判（"male" 命中 "female voice"）
    if voice.gender in _GENDER_KEYWORDS:
        for kw in _GENDER_KEYWORDS[voice.gender]:
            if kw.lower() in p_tokens:
                score += 2
                break
            # 中文关键词无法分词，改为对整段偏好文本做子串匹配
            if any("一" <= c <= "鿿" for c in kw) and kw.lower() in p:
                score += 2
                break
    return score


def match_voice(preference: str, voices: list[VoiceEntry]) -> tuple[VoiceEntry | None, list[VoiceEntry]]:
    if not voices:
        return None, []
    ranked = sorted(((_score(preference, v), v) for v in voices), key=lambda t: t[0], reverse=True)
    best_score, best = ranked[0]
    if best_score == 0:
        neutral = next((v for v in voices if v.gender == "neutral"), None)
        best = neutral or voices[0]
    alternatives = [v for _, v in ranked if (v.provider, v.id) != (best.provider, best.id)][:4]
    return best, alternatives


async def match_user_voice(
    db: AsyncSession,
    user_id: int,
    preference: str,
    language: str | None = None,
) -> VoiceMatchResponse:
    """按用户偏好文本在全部已配置 TTS 供应商的当前语言音色中挑选。"""
    chain = await resolve_provider_chain(db, user_id, "tts")
    voices = [voice for config in chain for voice in voices_for_provider(config.provider_name)]
    if language:
        voices = [voice for voice in voices if voice.language in {language, "multi"}]
    best, alternatives = match_voice(preference or "", voices)
    return VoiceMatchResponse(voice=best, alternatives=alternatives)


async def design_voice(db: AsyncSession, user_id: int, prompt: str, *, preview_text: str = "") -> VoiceDesignResult:
    """调用当前 TTS 供应商的音色设计能力生成自定义音色。"""
    chain = await resolve_provider_chain(db, user_id, "tts")
    if not chain:
        raise ValueError("no TTS provider configured")
    config = chain[0]
    cls = resolve(ServiceType.tts, config.provider_name)
    if cls.VOICE_DESIGN_GUIDE is None:
        raise ValueError(f"{config.provider_name} does not support voice design")
    provider = cls(config)
    return await provider.design_voice(prompt, preview_text=preview_text)
