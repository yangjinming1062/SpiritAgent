from typing import Literal

from fastapi import HTTPException, Query, Request
from features.conversation import session_management
from features.conversation.replies import VoiceBubbleView
from features.conversation.reply_audio import client_reply_bubbles, synthesize_reply_audio
from features.conversation.schemas import (
    DesktopSessionListResponse,
    DesktopSessionOperationResponse,
    DesktopSessionPatchRequest,
    DesktopSessionSearchResponse,
)
from infrastructure.config import SETTINGS
from infrastructure.database import DbSession
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError

from entrypoints.http.auth import CurrentUser
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routing import get_router

router = get_router()

logger = get_logger(__name__)


@router.post("/messages/{message_id}/voice/{bubble_index}")
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


@router.get("", response_model=DesktopSessionListResponse)
async def list_sessions(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=40, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    min_messages: int = Query(default=0, ge=0),
    archived: Literal["only", "exclude", "include"] = "exclude",
    order: Literal["recent", "created", "messages"] = "recent",
    include_subagents: bool = False,
) -> DesktopSessionListResponse:
    try:
        return await session_management.list_sessions(
            user.id,
            db,
            limit=limit,
            offset=offset,
            min_messages=min_messages,
            archived=archived,
            order=order,
            include_subagents=include_subagents,
        )
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/search", response_model=DesktopSessionSearchResponse)
async def search_sessions(
    user: CurrentUser,
    db: DbSession,
    q: str = Query(..., min_length=1, description="Substring to match against title, message content, and id"),
    archived: Literal["only", "exclude", "include"] = "exclude",
) -> DesktopSessionSearchResponse:
    """按标题、会话 id 或会话内任意消息检索；每用户最多 20 条（按最近活跃排序），q 必填且限长，不含子 Agent 会话。"""
    try:
        return await session_management.search_sessions(user.id, db, q=q, archived=archived)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/{session_id}", response_model=DesktopSessionOperationResponse)
async def patch_session(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
    body: DesktopSessionPatchRequest,
) -> DesktopSessionOperationResponse:
    try:
        return await session_management.patch_session(user.id, db, session_id, body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/{session_id}", response_model=DesktopSessionOperationResponse)
async def delete_session(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
) -> DesktopSessionOperationResponse:
    try:
        return await session_management.delete_session(user.id, db, session_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
