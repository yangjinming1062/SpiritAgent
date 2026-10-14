"""认识伙伴、时区和音色目录的事务入口。"""

from infrastructure.database import SESSION_LOCAL

from features.companion.interaction_stats import invalidate_user_interaction_stats
from features.companion.persona_service import get_onboarding_state, submit_onboarding_field
from features.companion.schemas import OnboardingStateResponse, VoiceMatchResponse, VoicesListResponse
from features.companion.voice_catalog import list_tts_voices, match_user_voice
from features.configuration.settings.timezone import record_user_timezone


async def set_user_timezone(user_id: int, timezone: str) -> None:
    async with SESSION_LOCAL() as db:
        if not await record_user_timezone(db, user_id, timezone):
            raise ValueError(f"unknown timezone: {timezone}")
        await db.commit()
    invalidate_user_interaction_stats(user_id)


async def onboarding_state(user_id: int) -> OnboardingStateResponse:
    async with SESSION_LOCAL() as db:
        return await get_onboarding_state(db, user_id)


async def submit_onboarding(
    user_id: int,
    field: str,
    value: str | None,
    *,
    expected_version: object,
) -> OnboardingStateResponse:
    async with SESSION_LOCAL() as db:
        return await submit_onboarding_field(db, user_id, field, value, expected_version=expected_version)


async def voice_catalog(user_id: int, language: str | None) -> VoicesListResponse:
    async with SESSION_LOCAL() as db:
        return await list_tts_voices(db, user_id, language=language)


async def match_voice(user_id: int, preference: str, language: str | None) -> VoiceMatchResponse:
    async with SESSION_LOCAL() as db:
        return await match_user_voice(db, user_id, preference, language=language)
