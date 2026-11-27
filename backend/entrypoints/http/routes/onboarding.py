from fastapi import HTTPException
from features.companion.persona_service import (
    PersonaConflictError,
    PersonaValidationError,
    complete_onboarding_profile,
    get_onboarding_state,
    submit_onboarding_field,
)
from features.companion.schemas import OnboardingProfileRequest, OnboardingStateResponse
from features.memory.contracts import MemoryConflictError
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


@router.put("/profile", response_model=OnboardingStateResponse)
async def put_onboarding_profile(
    user: CurrentUser, db: DbSession, body: OnboardingProfileRequest
) -> OnboardingStateResponse:
    try:
        return await complete_onboarding_profile(db, user.id, body)
    except (MemoryConflictError, PersonaConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
