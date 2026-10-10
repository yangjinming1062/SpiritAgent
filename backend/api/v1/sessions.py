import asyncio
from contextlib import AsyncExitStack
from typing import Literal

from common import get_router
from components import (
    SEARCH_INPUT_MAX_LEN,
    SESSION_PREVIEW_MAX_CHARS,
    SETTINGS,
    SQL_LIKE_ESCAPE_CHAR,
    DbSession,
    attachments_gc_session,
    get_logger,
)
from fastapi import HTTPException, Query, Request
from modules.auth import CurrentUser, User
from modules.conversation import (
    Conversation,
    DesktopSessionInfo,
    DesktopSessionListResponse,
    DesktopSessionOperationResponse,
    DesktopSessionPatchRequest,
    DesktopSessionSearchResponse,
    Message,
    VoiceBubbleView,
)
from modules.ws import emit_ws_event
from services.adapters.http import limiter
from services.application.generation import cleanup_user_media
from services.domains.assets import collect_message_asset_releases
from services.domains.conversation import (
    SPECIAL_KIND,
    SYSTEM_PRESET_CATALOG,
    cancel_reply_audio,
    client_reply_bubbles,
    message_contains_text,
    resolve_preset_meta,
    synthesize_reply_audio,
    visible_bubble_count,
)
from services.infrastructure.assets import user_asset_lock
from services.infrastructure.turn_ownership import conversation_is_running, conversation_lock
from sqlalchemy import String, asc, case, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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


# 列表/搜索预览关联子查询：取首条非空 user 消息并在 SQL 层截断，避免大文本/多模态造成整页 IO 放大
_preview_subquery = (
    select(func.substr(Message.content, 1, SESSION_PREVIEW_MAX_CHARS))
    .where(
        Message.conversation_id == Conversation.id,
        Message.role == "user",
        Message.content.isnot(None),
        Message.content != "",
    )
    .order_by(Message.id)
    .limit(1)
    .correlate(Conversation)
    .scalar_subquery()
    .label("preview")
)


def _conversation_to_session_info(
    conv: Conversation,
    *,
    msg_count: int,
    input_tok: int,
    output_tok: int,
    tool_count: int,
    preview: str | None,
) -> DesktopSessionInfo:
    preset = resolve_preset_meta(conv.system_preset_id) if not conv.is_automation else None
    return DesktopSessionInfo(
        id=str(conv.id),
        kind=conv.kind,
        title=conv.title,
        started_at=int(conv.created_at.timestamp() * 1000),
        last_active=int(conv.updated_at.timestamp() * 1000),
        message_count=msg_count,
        input_tokens=input_tok,
        output_tokens=output_tok,
        tool_call_count=tool_count,
        preview=preview,
        pinned=conv.pinned_at is not None,
        archived=conv.archived_at is not None,
        system_preset_id=conv.system_preset_id,
        system_preset_icon_key=preset.icon_key if preset else "task",
    )


async def _get_conversation_or_404(db: AsyncSession, user: User, session_id: str) -> Conversation:
    """按 id 查会话并强制归属校验，未命中抛 404。"""
    conv = await Conversation.by_session_id(db, session_id, user_id=user.id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return conv


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
    q = select(Conversation, _preview_subquery).where(Conversation.user_id == user.id)

    msg_stats = (
        select(
            Message.conversation_id,
            func.coalesce(func.sum(visible_bubble_count()), 0).label("msg_count"),
            func.coalesce(func.sum(Message.prompt_tokens), 0).label("input_tok"),
            func.coalesce(func.sum(Message.completion_tokens), 0).label("output_tok"),
            func.count(Message.id).filter(Message.tool_calls.isnot(None)).label("tool_count"),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.user_id == user.id)
        .group_by(Message.conversation_id)
        .subquery()
    )
    q = q.outerjoin(msg_stats, Conversation.id == msg_stats.c.conversation_id)
    q = q.add_columns(msg_stats.c.msg_count, msg_stats.c.input_tok, msg_stats.c.output_tok, msg_stats.c.tool_count)

    if archived == "only":
        q = q.where(Conversation.archived_at.isnot(None))
    elif archived == "exclude":
        q = q.where(Conversation.archived_at.is_(None))
        if not include_subagents:
            q = q.where(Conversation.parent_id.is_(None))
    # parent_id 只标记子 Agent 会话，派生会话照常列出；include_subagents 只约束未归档列表，归档视图保留子会话。
    if min_messages > 0:
        q = q.where(func.coalesce(msg_stats.c.msg_count, 0) >= min_messages)

    total_q = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()

    if archived == "only":
        # 归档视图按归档时间倒序，方便刚归档的先出现；order 参数对归档列表无意义。
        q = q.order_by(desc(Conversation.archived_at))
    else:
        # 系统预设、手动置顶优先，再按所选字段排序和分页。
        order_col = {
            "recent": desc(Conversation.updated_at),
            "created": desc(Conversation.created_at),
            "messages": desc(func.coalesce(msg_stats.c.msg_count, 0)),
        }[order]
        preset_rank = case(
            (
                Conversation.kind == SPECIAL_KIND,
                case(
                    *(
                        (Conversation.system_preset_id == preset_id, rank)
                        for rank, preset_id in enumerate(SYSTEM_PRESET_CATALOG)
                    ),
                    else_=99,
                ),
            ),
            else_=99,
        )
        q = q.order_by(
            desc(Conversation.kind == SPECIAL_KIND),
            preset_rank,
            asc(Conversation.pinned_at.is_(None)),
            desc(Conversation.pinned_at),
            order_col,
            Conversation.id,
        )

    rows = (await db.execute(q.offset(offset).limit(limit))).all()

    sessions = []
    for conv, preview, mc, it, ot, tc in rows:
        sessions.append(
            _conversation_to_session_info(
                conv,
                msg_count=int(mc or 0),
                input_tok=int(it or 0),
                output_tok=int(ot or 0),
                tool_count=int(tc or 0),
                preview=preview,
            ),
        )

    return DesktopSessionListResponse(limit=limit, offset=offset, total=total_q, sessions=sessions)


@router.get("/search", response_model=DesktopSessionSearchResponse)
async def search_sessions(
    user: CurrentUser,
    db: DbSession,
    q: str = Query(..., min_length=1, description="Substring to match against title, message content, and id"),
    archived: Literal["only", "exclude", "include"] = "exclude",
) -> DesktopSessionSearchResponse:
    """按标题、会话 id 或会话内任意消息检索；每用户最多 20 条（按最近活跃排序），q 必填且限长，不含子 Agent 会话。"""
    # 限制搜索词长度——LIKE 对多 KB 字符串慢，无 UX 理由让用户搜 10k 字符。
    if len(q) > SEARCH_INPUT_MAX_LEN:
        q = q[:SEARCH_INPUT_MAX_LEN]
    # 转义 SQL LIKE 元字符，保证用户输入按字面量匹配。
    escaped = (
        q.replace(SQL_LIKE_ESCAPE_CHAR, SQL_LIKE_ESCAPE_CHAR * 2)
        .replace("%", f"{SQL_LIKE_ESCAPE_CHAR}%")
        .replace("_", f"{SQL_LIKE_ESCAPE_CHAR}_")
    )
    pattern = f"%{escaped}%"

    # 内容候选按最近活跃排序，与最终列表同序；标题和 ID 匹配不占此额度。
    content_match_ids = (
        select(Conversation.id)
        .select_from(Message)
        .where(message_contains_text(q))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.user_id == user.id, Conversation.parent_id.is_(None))
        .group_by(Conversation.id)
        .order_by(desc(Conversation.updated_at), desc(Conversation.id))
        .limit(200)
        .correlate(None)
    )

    archived_filter = {
        "only": Conversation.archived_at.isnot(None),
        "exclude": Conversation.archived_at.is_(None),
        "include": None,
    }[archived]

    if archived_filter is not None:
        content_match_ids = content_match_ids.where(archived_filter)

    rows_query = (
        select(Conversation, _preview_subquery, func.coalesce(func.sum(visible_bubble_count()), 0).label("msg_count"))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == user.id,
            Conversation.parent_id.is_(None),
            or_(
                Conversation.title.ilike(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
                cast(Conversation.id, String).like(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
                Conversation.id.in_(content_match_ids),
            ),
        )
        .group_by(Conversation.id)
        .order_by(desc(Conversation.updated_at), desc(Conversation.id))
        .limit(20)
    )
    if archived_filter is not None:
        rows_query = rows_query.where(archived_filter)

    rows = (await db.execute(rows_query)).all()

    sessions = []
    for conv, preview, msg_count in rows:
        sessions.append(
            _conversation_to_session_info(
                conv,
                msg_count=int(msg_count or 0),
                input_tok=0,
                output_tok=0,
                tool_count=0,
                preview=preview,
            ),
        )

    return DesktopSessionSearchResponse(sessions=sessions)


@router.patch("/{session_id}", response_model=DesktopSessionOperationResponse)
async def patch_session(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
    body: DesktopSessionPatchRequest,
) -> DesktopSessionOperationResponse:
    conv = await _get_conversation_or_404(db, user, session_id)
    if conv.kind == SPECIAL_KIND or not conv.is_renamable:
        raise HTTPException(status_code=403, detail="System preset conversations cannot be modified or deleted")
    if body.title is not None:
        conv.title = body.title
    if body.pinned is not None:
        if body.pinned and conv.archived_at is not None:
            raise HTTPException(status_code=400, detail="Archived session cannot be pinned")
        conv.pinned_at = func.now() if body.pinned else None
    if body.archived is not None:
        if body.archived:
            conv.pinned_at = None
            conv.archived_at = func.now()
        else:
            conv.archived_at = None
    emit_ws_event(db, user_id=user.id, event_type="session.list_changed", payload={"session_id": str(conv.id)})
    await db.commit()
    return DesktopSessionOperationResponse(ok=True)


@router.delete("/{session_id}", response_model=DesktopSessionOperationResponse)
async def delete_session(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
) -> DesktopSessionOperationResponse:
    conv = await _get_conversation_or_404(db, user, session_id)
    if conv.kind == SPECIAL_KIND or not conv.is_deletable:
        raise HTTPException(status_code=403, detail="System preset conversations cannot be modified or deleted")
    descendants = select(Conversation.id).where(Conversation.parent_id == conv.id).cte("descendants", recursive=True)
    descendants = descendants.union(
        select(Conversation.id).join(descendants, Conversation.parent_id == descendants.c.id),
    )
    subtree = sorted({conv.id, *await db.scalars(select(descendants.c.id))})
    async with AsyncExitStack() as locks:
        for sid in subtree:
            # 取锁前发现回合在跑则直接 409，不排队等回合结束再删
            if conversation_is_running(str(sid)):
                raise HTTPException(status_code=409, detail="请先停止当前任务，再删除会话")
            await locks.enter_async_context(conversation_lock(str(sid)))
        async with user_asset_lock(user.id):
            removed = await collect_message_asset_releases(db, user.id, subtree)
            await db.delete(conv)
            emit_ws_event(
                db,
                user_id=user.id,
                event_type="session.list_changed",
                payload={"session_id": session_id, "deleted": True},
            )
            await db.commit()
    await cancel_reply_audio(user.id, removed)
    await cleanup_user_media(user.id)
    # 级联清理远端模式附件，尽力而为——文件系统错误（权限、磁盘满）不能让已删除会话行残留，日志记录后吞掉；gc_session 校验 session_id 形态并拒绝路径穿越，rmtree 仅作用于 SETTINGS.data_dir/desktop-attachments/。
    for deleted_id in subtree:
        try:
            await asyncio.to_thread(attachments_gc_session, str(deleted_id))
        except OSError:
            logger.warning("attachments_gc_session failed for session %s", deleted_id, exc_info=True)
    return DesktopSessionOperationResponse(ok=True)
