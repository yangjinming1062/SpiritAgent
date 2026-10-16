from typing import Annotated, Literal

from fastapi import Body, HTTPException
from features.companion.schemas import VoiceMatchResponse, VoicesListResponse
from features.companion.voice_catalog import list_tts_voices, match_user_voice, normalize_voice_language
from features.media.voice_contracts import VoiceConfirmResponse, VoicePreviewResponse, VoicePromptResponse
from features.media.voice_operations import cancel_voice_operation, run_voice_operation
from features.media.voices import clone_voice, confirm_voice, design_voice
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy
from pydantic import BaseModel, ConfigDict, Field
from workflows.voice_design import generate_voice_prompt

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router()


class VoiceMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preference: str = Field(min_length=1)
    language: str | None = None


class VoiceDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["design"]
    operation_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    preview_text: str = Field(min_length=1)
    provider: str = ""


class VoiceCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["clone"]
    operation_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    preview_text: str = Field(min_length=1)


class VoicePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1)
    current_prompt: str = ""
    feedback: str = ""
    preference: str = ""


class VoiceConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)


class VoiceCancelResponse(BaseModel):
    cancelled: bool


@router.get("", response_model=VoicesListResponse)
async def list_voices(user: CurrentUser, db: DbSession, language: str | None = None) -> VoicesListResponse:
    return await list_tts_voices(db, user.id, normalize_voice_language(language))


@router.post(
    "/match",
    response_model=VoiceMatchResponse,
)
async def post_voice_match(user: CurrentUser, db: DbSession, body: VoiceMatchRequest) -> VoiceMatchResponse:
    return await match_user_voice(db, user.id, body.preference, normalize_voice_language(body.language))


@router.post(
    "/candidates",
    response_model=VoicePreviewResponse,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def post_voice_candidate(
    user: CurrentUser,
    body: Annotated[VoiceDesignRequest | VoiceCloneRequest, Body(discriminator="kind")],
) -> VoicePreviewResponse:
    try:
        if body.kind == "design":
            return await run_voice_operation(
                user.id,
                body.operation_id,
                lambda: design_voice(
                    user.id,
                    body.prompt,
                    preview_text=body.preview_text,
                    provider_name=body.provider,
                ),
            )
        return await run_voice_operation(
            user.id,
            body.operation_id,
            lambda: clone_voice(
                user.id,
                body.sample_id,
                preview_text=body.preview_text,
            ),
        )
    except (ValueError, MissingLlmConfigError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/prompt",
    response_model=VoicePromptResponse,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def post_voice_prompt(user: CurrentUser, body: VoicePromptRequest) -> VoicePromptResponse:
    try:
        return await run_voice_operation(
            user.id,
            body.operation_id,
            lambda: generate_voice_prompt(
                user.id,
                body.current_prompt,
                body.feedback,
                body.preference,
            ),
        )
    except (ValueError, MissingLlmConfigError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/candidates/{candidate_id}/confirmation",
    response_model=VoiceConfirmResponse,
)
async def post_voice_confirmation(
    user: CurrentUser,
    candidate_id: str,
    body: VoiceConfirmRequest,
) -> VoiceConfirmResponse:
    try:
        return await confirm_voice(user.id, candidate_id, body.expected_version)
    except (ValueError, MissingLlmConfigError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete(
    "/operations/{operation_id}",
    response_model=VoiceCancelResponse,
)
async def delete_voice_operation(user: CurrentUser, operation_id: str) -> VoiceCancelResponse:
    try:
        return VoiceCancelResponse(cancelled=cancel_voice_operation(user.id, operation_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
