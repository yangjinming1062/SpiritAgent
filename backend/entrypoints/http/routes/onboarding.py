from fastapi import HTTPException
from features.companion.persona_service import PersonaValidationError, get_onboarding_state, submit_onboarding_field
from features.companion.schemas import OnboardingStateResponse
from infrastructure.database import DbSession
from pydantic import BaseModel, ConfigDict, Field

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/onboarding")


class OnboardingPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1)
    value: str | None = None


@router.get("", response_model=OnboardingStateResponse)
async def get_onboarding(user: CurrentUser, db: DbSession) -> OnboardingStateResponse:
    return await get_onboarding_state(db, user.id)


@router.patch("", response_model=OnboardingStateResponse)
async def patch_onboarding(user: CurrentUser, db: DbSession, body: OnboardingPatchRequest) -> OnboardingStateResponse:
    try:
        return await submit_onboarding_field(db, user.id, body.field, body.value)
    except PersonaValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
