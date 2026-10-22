from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException, Path, Query, Request
from features.conversation import session_management
from features.conversation.fork import ForkNotAllowedError, SourceNotFoundError
from features.conversation.http_contracts import (
    CommandDispatchRequest,
    CommandDispatchResult,
    PromptSubmitRequest,
    PromptSubmitResult,
    SessionCompressionResult,
    SessionSnapshotResponse,
    SessionUndoRequest,
    SessionUndoResult,
    TurnCancelResult,
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
from features.conversation.session_operations import (
    ConversationNotFoundError,
    ForkAttachmentError,
    create_conversation,
    ensure_main_conversation,
    fork_conversation,
    get_conversation,
    history_page,
)
from infrastructure.config import SETTINGS
from infrastructure.constants import POSTGRES_INT4_MAX, POSTGRES_INT8_MAX
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy
from runtime.conversation import SessionCreateResult
from runtime.desktop.connection import UserEventPublisher
from runtime.presets import SYSTEM_PRESET_CATALOG
from workflows.chat.operations import chat_operations
from workflows.chat.session_resources import list_session_resources, patch_session_resource, session_resource

from entrypoints.http.auth import CurrentActor, CurrentUser
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routing import get_router

router = get_router(remote=True)


@router.post(
    "/{session_id}/turns",
    operation_id="sessions.submit_turn",
    response_model=PromptSubmitResult,
    openapi_extra=operation_policy(upload="chat"),
)
async def submit_turn(actor: CurrentActor, session_id: str, body: PromptSubmitRequest) -> PromptSubmitResult:
    result = await chat_operations(actor).submit(session_id, body)
    return PromptSubmitResult.model_validate(result)


@router.get(
    "/{session_id}/turns/{request_id}",
    operation_id="sessions.get_turn",
    response_model=PromptSubmitResult,
)
async def get_turn(actor: CurrentActor, session_id: str, request_id: UUID) -> PromptSubmitResult:
    result = await chat_operations(actor).receipt({"session_id": session_id, "request_id": str(request_id)})
    return PromptSubmitResult.model_validate(result)


@router.post(
    "/{session_id}/turns/{request_id}/cancel",
    operation_id="sessions.cancel_turn",
    response_model=TurnCancelResult,
)
async def cancel_turn(actor: CurrentActor, session_id: str, request_id: UUID) -> TurnCancelResult:
    result = await chat_operations(actor).cancel({"session_id": session_id, "request_id": str(request_id)})
    return TurnCancelResult.model_validate(result)


@router.get(
    "/{session_id}/snapshot",
    operation_id="sessions.snapshot",
    response_model=SessionSnapshotResponse,
)
async def get_session_snapshot(
    actor: CurrentActor,
    session_id: str,
    stream_id: str | None = Query(default=None, max_length=128),
) -> SessionSnapshotResponse:
    params = {
        "session_id": session_id,
        "stream_id": stream_id,
    }
    result = await chat_operations(actor).snapshot(params)
    return SessionSnapshotResponse.model_validate(result)


@router.post(
    "/{session_id}/history/undo",
    operation_id="sessions.undo",
    response_model=SessionUndoResult,
)
async def undo_session_history(actor: CurrentActor, session_id: str, body: SessionUndoRequest) -> SessionUndoResult:
    return await chat_operations(actor).undo({**body.model_dump(exclude_unset=True), "session_id": session_id})


@router.post(
    "/{session_id}/history/compression",
    operation_id="sessions.compress",
    response_model=SessionCompressionResult,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def compress_session_history(actor: CurrentActor, session_id: str) -> SessionCompressionResult:
    result = await chat_operations(actor).compress({"session_id": session_id})
    return SessionCompressionResult.model_validate(result)


@router.post(
    "/{session_id}/commands",
    operation_id="sessions.command",
    response_model=CommandDispatchResult,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def dispatch_session_command(
    actor: CurrentActor,
    session_id: str,
    body: CommandDispatchRequest,
) -> CommandDispatchResult:
    result = await chat_operations(actor).command({**body.model_dump(exclude_unset=True), "session_id": session_id})
    return CommandDispatchResult.model_validate(result)


@router.post(
    "/messages/{message_id}/voice/{bubble_index}",
    openapi_extra=operation_policy(wait_for_model=True),
)
@limiter.limit(lambda: f"{SETTINGS.media_tts_rate_limit_per_minute}/minute")
async def retry_voice_bubble(
    request: Request,
    user: CurrentUser,
    message_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
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
    offset: int = Query(default=0, ge=0, le=POSTGRES_INT8_MAX),
    min_messages: int = Query(default=0, ge=0, le=POSTGRES_INT8_MAX),
    archived: Literal["only", "exclude", "include"] = "exclude",
    order: Literal["recent", "created", "messages"] = "recent",
    include_subagents: bool = False,
    q: str | None = Query(default=None, max_length=512, pattern=r"^[^\x00]*$"),
) -> DesktopSessionListResponse:
    return await list_session_resources(
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
    before_id: int | None = Query(default=None, gt=0, le=POSTGRES_INT4_MAX),
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
