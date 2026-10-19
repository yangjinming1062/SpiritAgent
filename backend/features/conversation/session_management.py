import asyncio
import json
from contextlib import AsyncExitStack
from typing import Literal

from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.attachments import gc_session as attachments_gc_session
from infrastructure.constants import SEARCH_INPUT_MAX_LEN, SESSION_PREVIEW_MAX_CHARS, SQL_LIKE_ESCAPE_CHAR
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from runtime.conversation import decode_session_settings
from runtime.events.emit import emit_ws_event
from runtime.presets import resolve_preset_meta
from runtime.turn_ownership import conversation_is_running, conversation_lock
from sqlalchemy import BigInteger, String, asc, case, cast, desc, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.asset_releases import collect_message_asset_releases
from features.conversation.formatting import message_contains_text, visible_bubble_count
from features.conversation.main_conversation import SPECIAL_KIND
from features.conversation.models import Conversation, Message, parse_session_id
from features.conversation.reply_audio import cancel_reply_audio
from features.conversation.schemas import (
    DesktopSessionInfo,
    DesktopSessionListResponse,
    DesktopSessionOperationResponse,
    DesktopSessionPatchRequest,
)
from features.conversation.snapshots import ConversationSnapshot
from features.conversation.turn_access import conversation_snapshot
from features.media.video_jobs import cleanup_user_media

logger = get_logger(__name__)


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
    companion_name: str | None,
) -> DesktopSessionInfo:
    preset = resolve_preset_meta(conv.system_preset_id) if not conv.is_automation else None
    return DesktopSessionInfo(
        id=str(conv.id),
        kind=conv.kind,
        title=companion_name if conv.kind == SPECIAL_KIND else conv.title,
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


async def _get_conversation_or_404(db: AsyncSession, user_id: int, session_id: str) -> Conversation:
    """按 id 查会话并强制归属校验，未命中抛 404。"""
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)
    if conv is None:
        raise RequestError(status_code=404, message="Session not found")
    return conv


async def list_sessions(
    user_id: int,
    db: AsyncSession,
    limit: int = 40,
    offset: int = 0,
    min_messages: int = 0,
    archived: Literal["only", "exclude", "include"] = "exclude",
    order: Literal["recent", "created", "messages"] = "recent",
    include_subagents: bool = False,
    query: str | None = None,
    companion_name: str | None = None,
) -> DesktopSessionListResponse:
    q = select(Conversation, _preview_subquery).where(Conversation.user_id == user_id)

    msg_stats = (
        select(
            Message.conversation_id,
            func.coalesce(func.sum(visible_bubble_count()), 0).label("msg_count"),
            func.coalesce(func.sum(Message.prompt_tokens), 0).label("input_tok"),
            func.coalesce(func.sum(Message.completion_tokens), 0).label("output_tok"),
            func.count(Message.id).filter(Message.tool_calls.isnot(None)).label("tool_count"),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.user_id == user_id)
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
    if query:
        query = query[:SEARCH_INPUT_MAX_LEN]
        escaped = (
            query.replace(SQL_LIKE_ESCAPE_CHAR, SQL_LIKE_ESCAPE_CHAR * 2)
            .replace("%", f"{SQL_LIKE_ESCAPE_CHAR}%")
            .replace("_", f"{SQL_LIKE_ESCAPE_CHAR}_")
        )
        pattern = f"%{escaped}%"
        display_title = case(
            (Conversation.kind == SPECIAL_KIND, literal(companion_name, String())),
            else_=Conversation.title,
        )
        content_ids = (
            select(Message.conversation_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == user_id, message_contains_text(query))
            .distinct()
            .correlate(None)
        )
        q = q.where(
            or_(
                display_title.ilike(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
                cast(Conversation.id, String).like(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
                Conversation.id.in_(content_ids),
            ),
        )
    if min_messages > 0:
        q = q.where(func.coalesce(msg_stats.c.msg_count, 0) >= literal(min_messages, BigInteger()))

    total_q = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()

    if archived == "only":
        # 归档视图按归档时间倒序，方便刚归档的先出现；order 参数对归档列表无意义。
        q = q.order_by(desc(Conversation.archived_at))
    else:
        # 系统对话、手动置顶优先，再按所选字段排序和分页。
        order_col = {
            "recent": desc(Conversation.updated_at),
            "created": desc(Conversation.created_at),
            "messages": desc(func.coalesce(msg_stats.c.msg_count, 0)),
        }[order]
        q = q.order_by(
            desc(Conversation.kind == SPECIAL_KIND),
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
                companion_name=companion_name,
            ),
        )

    return DesktopSessionListResponse(limit=limit, offset=offset, total=total_q, sessions=sessions)


async def patch_session(
    user_id: int,
    db: AsyncSession,
    session_id: str,
    body: DesktopSessionPatchRequest,
) -> ConversationSnapshot:
    conversation_id = parse_session_id(session_id) if session_id.isdecimal() else None
    if conversation_id is None:
        raise RequestError(status_code=404, message="Session not found")
    conv = await db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        .with_for_update(),
    )
    if conv is None:
        raise RequestError(status_code=404, message="Session not found")
    metadata = body.model_dump(exclude_unset=True, exclude={"settings"})
    if metadata and (conv.kind == SPECIAL_KIND or not conv.is_renamable):
        raise RequestError(status_code=403, message="System preset conversations cannot be modified or deleted")
    if body.title is not None:
        conv.title = body.title
    if body.pinned is not None:
        if body.pinned and conv.archived_at is not None:
            raise RequestError(status_code=400, message="Archived session cannot be pinned")
        conv.pinned_at = func.now() if body.pinned else None
    if body.archived is not None:
        if body.archived:
            conv.pinned_at = None
            conv.archived_at = func.now()
        else:
            conv.archived_at = None
    if body.settings is not None:
        settings = decode_session_settings(conv.settings_json)
        for key, value in body.settings.model_dump(exclude_unset=True).items():
            if value is None:
                settings.pop(key, None)
            else:
                settings[key] = value
        conv.settings_json = json.dumps(settings, ensure_ascii=False)
    if metadata:
        emit_ws_event(db, user_id=user_id, event_type="session.list_changed", payload={"session_id": str(conv.id)})
    await db.commit()
    return conversation_snapshot(conv)


async def delete_session(user_id: int, db: AsyncSession, session_id: str) -> DesktopSessionOperationResponse:
    conv = await _get_conversation_or_404(db, user_id, session_id)
    if conv.kind == SPECIAL_KIND or not conv.is_deletable:
        raise RequestError(status_code=403, message="System preset conversations cannot be modified or deleted")
    descendants = select(Conversation.id).where(Conversation.parent_id == conv.id).cte("descendants", recursive=True)
    descendants = descendants.union(
        select(Conversation.id).join(descendants, Conversation.parent_id == descendants.c.id),
    )
    subtree = sorted({conv.id, *await db.scalars(select(descendants.c.id))})
    async with AsyncExitStack() as locks:
        for sid in subtree:
            # 取锁前发现回合在跑则直接 409，不排队等回合结束再删
            if conversation_is_running(str(sid)):
                raise RequestError(status_code=409, message="请先停止当前任务，再删除会话")
            await locks.enter_async_context(conversation_lock(str(sid)))
        async with user_asset_lock(user_id):
            removed = await collect_message_asset_releases(db, user_id, subtree)
            await db.delete(conv)
            emit_ws_event(
                db,
                user_id=user_id,
                event_type="session.list_changed",
                payload={"session_id": session_id, "deleted": True},
            )
            await db.commit()
    await cancel_reply_audio(user_id, removed)
    await cleanup_user_media(user_id)
    # 级联清理远端模式附件，尽力而为——文件系统错误（权限、磁盘满）不能让已删除会话行残留，日志记录后吞掉；gc_session 校验 session_id 形态并拒绝路径穿越，rmtree 仅作用于 SETTINGS.data_dir/desktop-attachments/。
    for deleted_id in subtree:
        try:
            await asyncio.to_thread(attachments_gc_session, str(deleted_id))
        except OSError:
            logger.warning("attachments_gc_session failed for session %s", deleted_id, exc_info=True)
    return DesktopSessionOperationResponse(ok=True)
