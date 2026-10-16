from typing import Literal

from fastapi import HTTPException, Query, Request
from features.conversation import session_management
from features.conversation.fork import ForkNotAllowedError, SourceNotFoundError
from features.conversation.gateway_queries import (
    ConversationNotFoundError,
    ForkAttachmentError,
    create_conversation,
    ensure_main_conversation,
    fork_conversation,
    get_conversation,
    history_page,
)
from features.conversation.replies import VoiceBubbleView
from features.conversation.reply_audio import client_reply_bubbles, synthesize_reply_audio
from features.conversation.schemas import (
    DesktopSessionListResponse,
    DesktopSessionOperationResponse,
    DesktopSessionPatchRequest,
    SessionCreateRequest,
    SessionForkRequest,
    SessionHistoryResponse,
)
from infrastructure.config import SETTINGS
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy
from infrastructure.logger import get_logger
from runtime.conversation import SessionCreateResult
from runtime.desktop.connection import UserEventPublisher
from runtime.presets import SYSTEM_PRESET_CATALOG
from workflows.chat.session_resources import patch_session_resource, session_resource

from entrypoints.http.auth import CurrentUser
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routing import get_router

router = get_router(remote=True)

logger = get_logger(__name__)


@router.post(
    "/messages/{message_id}/voice/{bubble_index}",
    openapi_extra=operation_policy(wait_for_model=True),
)
@limiter.limit(lambda: f"{SETTINGS.media_tts_rate_limit_per_minute}/minute")
async def retry_voice_bubble(
    request: Request,
    user: CurrentUser,
    message_id: int,
    bubble_index: int,
) -> VoiceBubbleView:
    try:
        reply = await synthesize_reply_audio(user.id, message_id, bubble_index=bubble_index)
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="Voice synthesis is busy; retry shortly") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Voice message not found") from exc
    return VoiceBubbleView.model_validate(client_reply_bubbles(reply)[bubble_index])


@router.get(
    "",
    response_model=DesktopSessionListResponse,
)
async def list_sessions(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=40, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    min_messages: int = Query(default=0, ge=0),
    archived: Literal["only", "exclude", "include"] = "exclude",
    order: Literal["recent", "created", "messages"] = "recent",
    include_subagents: bool = False,
    q: str | None = Query(default=None, max_length=512),
) -> DesktopSessionListResponse:
    return await session_management.list_sessions(
        user.id,
        db,
        limit=limit,
        offset=offset,
        min_messages=min_messages,
        archived=archived,
        order=order,
        include_subagents=include_subagents,
        query=q,
    )


@router.post("", response_model=SessionCreateResult)
async def create_session(user: CurrentUser, body: SessionCreateRequest) -> SessionCreateResult:
    if body.system_preset_id not in SYSTEM_PRESET_CATALOG:
        raise HTTPException(status_code=422, detail="Unknown system preset")
    conv = await create_conversation(user.id, body.system_preset_id)
    await UserEventPublisher(user.id).push_event("session.list_changed", {"session_id": str(conv.id)})
    return await session_resource(user.id, conv)


@router.get(
    "/main",
    response_model=SessionCreateResult,
)
async def get_main_session(user: CurrentUser) -> SessionCreateResult:
    return await session_resource(user.id, await ensure_main_conversation(user.id))


@router.get(
    "/{session_id}",
    response_model=SessionCreateResult,
)
async def get_session(user: CurrentUser, session_id: str) -> SessionCreateResult:
    try:
        return await session_resource(user.id, await get_conversation(user.id, session_id))
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{session_id}/forks",
    response_model=SessionCreateResult,
)
async def fork_session(user: CurrentUser, session_id: str, body: SessionForkRequest) -> SessionCreateResult:
    try:
        conv, _ = await fork_conversation(user.id, session_id, body.source_message_id)
    except (ConversationNotFoundError, SourceNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ForkNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ForkAttachmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await UserEventPublisher(user.id).push_event("session.list_changed", {"session_id": str(conv.id)})
    return await session_resource(user.id, conv)


@router.get(
    "/{session_id}/history",
    response_model=SessionHistoryResponse,
)
async def get_session_history(
    user: CurrentUser,
    session_id: str,
    before_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> SessionHistoryResponse:
    try:
        return SessionHistoryResponse.model_validate(await history_page(user.id, session_id, before_id, limit))
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch(
    "/{session_id}",
    response_model=SessionCreateResult,
)
async def patch_session(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
    body: DesktopSessionPatchRequest,
) -> SessionCreateResult:
    try:
        return await patch_session_resource(user.id, db, session_id, body)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/{session_id}",
    response_model=DesktopSessionOperationResponse,
)
async def delete_session(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
) -> DesktopSessionOperationResponse:
    return await session_management.delete_session(user.id, db, session_id)
