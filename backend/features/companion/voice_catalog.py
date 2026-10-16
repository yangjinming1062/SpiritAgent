from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.providers.base import ProviderConfig, ServiceType
from infrastructure.ai.providers.registry import try_resolve
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.voice_catalog import voices_for_provider
from infrastructure.ai.voice_types import VoiceEntry
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.schemas import VoiceMatchResponse, VoicesListResponse
from features.media.voice_queries import confirmed_custom_voices

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


def _chain_voices(chain: list[ProviderConfig], language: str | None) -> list[VoiceEntry]:
    """按能力链顺序汇总音色；指定语言时保留该语言与多语言音色。"""
    voices_by_id: dict[tuple[str, str], VoiceEntry] = {}
    for config in chain:
        adapter = try_resolve(ServiceType.tts, config.provider_name)
        if adapter is None or (adapter.BUILTIN_VOICE_MODEL is not None and config.model != adapter.BUILTIN_VOICE_MODEL):
            continue
        for voice in voices_for_provider(config.provider_name):
            voices_by_id.setdefault((voice.provider, voice.id), voice)
    voices = list(voices_by_id.values())
    return [voice for voice in voices if voice.language in {language, "multi"}] if language else voices


async def list_tts_voices(db: AsyncSession, user_id: int, language: str | None = None) -> VoicesListResponse:
    """返回全部已配置 TTS 供应商的音色目录，并按当前界面语言收敛。"""
    chain = await resolve_provider_chain(db, user_id, "tts", task=AiTaskKey.companion_voice)
    voices = _sort_voices_by_language(_chain_voices(chain, language))
    design_chain = await resolve_provider_chain(db, user_id, "tts", task=AiTaskKey.voice_design)
    design_cls = next(
        (
            cls
            for config in design_chain
            if (cls := try_resolve(ServiceType.tts, config.provider_name)) is not None
            and cls.VOICE_DESIGN_GUIDE is not None
        ),
        None,
    )
    guide = design_cls.VOICE_DESIGN_GUIDE if design_cls else None
    prompt_chain = await resolve_provider_chain(db, user_id, "llm", task=AiTaskKey.voice_design)
    clone_chain = await resolve_provider_chain(db, user_id, "tts", task=AiTaskKey.voice_clone)
    preview_chain = await resolve_provider_chain(db, user_id, "tts", task=AiTaskKey.speech_preview)
    clone_cards = {str(config.model_card_id) for config in clone_chain}
    available_models = {
        (config.provider_name, config.model, str(config.model_card_id))
        for config in chain
        if config.model != "mimo-v2.5-tts-voiceclone" or str(config.model_card_id) in clone_cards
    } & {(config.provider_name, config.model, str(config.model_card_id)) for config in preview_chain}
    return VoicesListResponse(
        providers=list(dict.fromkeys(config.provider_name for config in chain)),
        voices=voices,
        supports_voice_design=guide is not None,
        voice_design_guide=guide or "",
        supports_voice_prompt_generation=guide is not None and bool(prompt_chain),
        supports_voice_clone=bool(clone_chain),
        custom_voices=await confirmed_custom_voices(db, user_id, available_models),
    )


def _score(preference: str, voice: VoiceEntry) -> int:
    p = preference.lower()
    p_tokens = p.split()
    score = 0
    for tag in voice.tags:
        t = tag.lower()
        if t in p:
            score += 2
        elif any(tok in t for tok in p_tokens):
            score += 1
    if voice.label.lower() in p:
        score += 2
    if voice.language and voice.language != "multi":
        kws = _LANG_KEYWORDS.get(voice.language, [])
        if any(kw.lower() in p for kw in kws):
            score += 2
    if voice.gender in _GENDER_KEYWORDS:
        for kw in _GENDER_KEYWORDS[voice.gender]:
            if kw.lower() in p_tokens:
                score += 2
                break
            # 中文关键词对整段偏好文本做子串匹配。
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
    voices = _chain_voices(
        await resolve_provider_chain(db, user_id, "tts", task=AiTaskKey.companion_voice),
        language,
    )
    best, alternatives = match_voice(preference or "", voices)
    return VoiceMatchResponse(voice=best, alternatives=alternatives)
