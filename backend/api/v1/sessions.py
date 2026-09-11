from typing import Literal

from common import get_router
from components import (
    JSONRPC_INVALID_PARAMS,
    SEARCH_INPUT_MAX_LEN,
    SESSION_PREVIEW_MAX_CHARS,
    SETTINGS,
    SQL_LIKE_ESCAPE_CHAR,
    DbSession,
    attachments_gc_session,
    get_logger,
    temp_files_gc_session,
)
from fastapi import HTTPException, Query
from modules.auth import CurrentUser, User
from modules.conversation import (
    Conversation,
    DesktopSessionForkRequest,
    DesktopSessionForkResponse,
    DesktopSessionInfo,
    DesktopSessionListResponse,
    DesktopSessionMessagesResponse,
    DesktopSessionOperationResponse,
    DesktopSessionPatchRequest,
    DesktopSessionSearchResponse,
    DesktopSessionUndoRequest,
    DesktopSessionUndoResponse,
    Message,
)
from services.conversation import (
    SPECIAL_KIND,
    ForkNotAllowedError,
    SourceNotFoundError,
    build_session_messages,
    fork_conversation_from_message,
    resolve_preset_meta,
)
from services.gateway import do_session_undo
from services.ws import MANAGER, JsonRpcError
from sqlalchemy import String, asc, case, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

router = get_router()

logger = get_logger(__name__)


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
    preset = resolve_preset_meta(conv.system_preset_id)
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
        cwd=conv.cwd,
        pinned=conv.pinned_at is not None,
        archived=conv.archived_at is not None,
        lineage_root_id=str(conv.parent_id) if conv.parent_id is not None else None,
        system_preset_id=conv.system_preset_id,
        system_preset_icon_key=preset.icon_key,
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
    limit: int = 40,
    offset: int = 0,
    min_messages: int = 0,
    archived: Literal["only", "exclude", "include"] = "exclude",
    order: Literal["recent", "created", "messages"] = "recent",
    include_subagents: bool = False,
) -> DesktopSessionListResponse:
    q = select(Conversation, _preview_subquery).where(Conversation.user_id == user.id)

    msg_stats = (
        select(
            Message.conversation_id,
            func.count(Message.id).label("msg_count"),
            func.coalesce(func.sum(Message.prompt_tokens), 0).label("input_tok"),
            func.coalesce(func.sum(Message.completion_tokens), 0).label("output_tok"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    tool_stats = (
        select(Message.conversation_id, func.count(Message.id).label("tool_count"))
        .where(Message.tool_calls.isnot(None))
        .group_by(Message.conversation_id)
        .subquery()
    )

    q = q.outerjoin(msg_stats, Conversation.id == msg_stats.c.conversation_id)
    q = q.outerjoin(tool_stats, Conversation.id == tool_stats.c.conversation_id)

    q = q.add_columns(msg_stats.c.msg_count, msg_stats.c.input_tok, msg_stats.c.output_tok, tool_stats.c.tool_count)

    if archived == "only":
        q = q.where(Conversation.archived_at.isnot(None))
    elif archived == "exclude":
        # 默认（include_subagents=False）仅 parent_id IS NULL 的顶层会话；开启后显示主+子代理，但两者都排除已归档行。
        q = q.where(Conversation.archived_at.is_(None))
        if not include_subagents:
            q = q.where(Conversation.parent_id.is_(None))
    # 其他 archived 取值由 Literal 在 HTTP 边界 422 拦截，此处 fallthrough 实际不可达，保留 no-op 供未来扩展。
    # include_subagents 仅作用于 archived="exclude"；"only"/"include" 路径忽略它（子代理导航走搜索端点与直链，不在 archived 切换 UI 内）。

    if min_messages > 0:
        q = q.where(func.coalesce(msg_stats.c.msg_count, 0) >= min_messages)

    total_q = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()

    if archived == "only":
        # 归档视图按归档时间倒序，方便刚归档的先出现；order 参数对归档列表无意义。
        q = q.order_by(desc(Conversation.archived_at))
    else:
        # 排序链：特殊对话恒第一（companion/developer/product_manager/copywriter/language_teacher 等系统预设对话，按系统预设 ID 排序对齐），手动置顶次之（pinned_at 新的在前），再按所选 order。置顶集占结果前缀，limit 分页不会截断它。
        order_col = {
            "recent": desc(Conversation.updated_at),
            "created": desc(Conversation.created_at),
            "messages": desc(func.coalesce(msg_stats.c.msg_count, 0)),
        }[order]
        # 5 套系统预设排序槽（仅作用于系统预设对话，普通对话不按预设重排）
        preset_rank = case(
            (
                Conversation.kind == SPECIAL_KIND,
                case(
                    (Conversation.system_preset_id == "companion", 0),
                    (Conversation.system_preset_id == "developer", 1),
                    (Conversation.system_preset_id == "product_manager", 2),
                    (Conversation.system_preset_id == "copywriter", 3),
                    (Conversation.system_preset_id == "language_teacher", 4),
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
    """按标题、会话 id 或会话内任意消息检索；每用户最多 20 条（按最近活跃排序），q 必填且限长。"""
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

    # 拆两条查询：标题/id 匹配 vs 包含匹配消息的会话；Python 端合并——内容扫描路径封顶独立会话 id，分两条读路径便于限流与可读性。
    title_match_ids = [
        row[0]
        for row in (
            await db.execute(
                select(Conversation.id).where(
                    Conversation.user_id == user.id,
                    or_(
                        Conversation.title.ilike(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
                        cast(Conversation.id, String).like(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
                    ),
                ),
            )
        ).all()
    ]
    # 内容扫描是最贵路径（messages.content 全表 LIKE），封顶 200 个独立会话 id。
    content_match_ids = [
        row[0]
        for row in (
            await db.execute(
                select(Message.conversation_id)
                .where(Message.content.ilike(pattern, escape=SQL_LIKE_ESCAPE_CHAR))
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(Conversation.user_id == user.id)
                .distinct()
                .limit(200),
            )
        ).all()
    ]

    merged_ids = set(title_match_ids) | set(content_match_ids)
    if not merged_ids:
        return DesktopSessionSearchResponse(sessions=[])

    archived_filter = {
        "only": Conversation.archived_at.isnot(None),
        "exclude": Conversation.archived_at.is_(None),
        "include": None,
    }[archived]

    rows_query = (
        select(Conversation, _preview_subquery, func.count(Message.id).label("msg_count"))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.id.in_(merged_ids))
        .group_by(Conversation.id)
        .order_by(desc(Conversation.updated_at))
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


@router.get("/{session_id}/messages", response_model=DesktopSessionMessagesResponse)
async def get_session_messages(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
    before_id: int | None = Query(
        default=None,
        ge=0,
        description="取 id < before_id 的更早历史，配合 next_cursor 翻页",
    ),
    limit: int | None = Query(default=None, ge=1, le=1000, description="单次返回条数上限"),
) -> DesktopSessionMessagesResponse:
    conv = await _get_conversation_or_404(db, user, session_id)
    if limit is None:
        messages = await build_session_messages(conv.id, db, before_id=before_id, include_id=True)
        next_cursor = None
    else:
        messages = await build_session_messages(
            conv.id,
            db,
            before_id=before_id,
            limit=limit + 1,
            desc=True,
            include_id=True,
        )
        has_more = len(messages) > limit
        messages = messages[:limit]
        messages.reverse()
        next_cursor = str(messages[0]["id"]) if has_more and messages else None
    return DesktopSessionMessagesResponse(session_id=str(conv.id), messages=messages, next_cursor=next_cursor)


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
    await db.commit()
    return DesktopSessionOperationResponse(ok=True)


@router.post("/{session_id}/fork", response_model=DesktopSessionForkResponse)
async def fork_session(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
    body: DesktopSessionForkRequest,
) -> DesktopSessionForkResponse:
    """从指定消息派生新会话。"""
    try:
        result = await fork_conversation_from_message(db, user.id, session_id, body.source_message_id)
    except ForkNotAllowedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except SourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return DesktopSessionForkResponse(**result)


@router.post("/{session_id}/undo-to-message", response_model=DesktopSessionUndoResponse)
async def undo_to_message(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
    body: DesktopSessionUndoRequest,
) -> DesktopSessionUndoResponse:
    """截断会话至指定消息，并返回该消息作为草稿。"""
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="confirmed=true required")
    try:
        result = await do_session_undo(
            user.id,
            session_id,
            body.source_message_id,
            runtime_sessions=MANAGER.get_runtime_sessions(user.id),
            dispatcher=MANAGER.get_dispatcher(user.id),
        )
        return DesktopSessionUndoResponse(**result)
    except JsonRpcError as e:
        if e.code == JSONRPC_INVALID_PARAMS:
            raise HTTPException(status_code=400, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{session_id}", response_model=DesktopSessionOperationResponse)
async def delete_session(
    user: CurrentUser,
    db: DbSession,
    session_id: str,
) -> DesktopSessionOperationResponse:
    conv = await _get_conversation_or_404(db, user, session_id)
    if conv.kind == SPECIAL_KIND or not conv.is_deletable:
        raise HTTPException(status_code=403, detail="System preset conversations cannot be modified or deleted")
    deleted_id = conv.id
    await db.delete(conv)
    await db.commit()
    # 级联清理远端模式附件，尽力而为——文件系统错误（权限、磁盘满）不能让已删除会话行残留，日志记录后吞掉；gc_session 校验 session_id 形态并拒绝路径穿越，rmtree 仅作用于 SETTINGS.data_dir/desktop-attachments/。
    try:
        attachments_gc_session(SETTINGS.data_dir, str(deleted_id))
    except Exception:
        logger.warning("attachments_gc_session failed for session %s", deleted_id, exc_info=True)
    try:
        temp_files_gc_session(str(deleted_id))
    except Exception:
        logger.warning("temp_files_gc failed for session %s", deleted_id, exc_info=True)
    return DesktopSessionOperationResponse(ok=True)
