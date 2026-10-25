import asyncio
import base64
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any, Literal

from infrastructure.api_ids import POSTGRES_BIGINT_MAX, ResourceId
from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.attachments import gc_session as attachments_gc_session
from infrastructure.constants import SEARCH_INPUT_MAX_LEN, SESSION_PREVIEW_MAX_CHARS, SQL_LIKE_ESCAPE_CHAR
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from runtime.events.emit import emit_ws_event
from runtime.presets import resolve_preset_meta
from runtime.turn_ownership import conversation_is_running, conversation_lock
from sqlalchemy import (
    BigInteger,
    ColumnElement,
    DateTime,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.asset_releases import collect_message_asset_releases
from features.conversation.formatting import message_contains_text
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
        Message.role == literal_column("'user'"),
        Message.content.isnot(None),
        Message.content != literal_column("''"),
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


class _SessionCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["archived", "recent", "created", "messages"]
    identifier: ResourceId
    system: bool = False
    pinned_at: AwareDatetime | None = None
    order_time: AwareDatetime | None = None
    message_count: int | None = Field(default=None, ge=0, le=POSTGRES_BIGINT_MAX)


def _session_cursor(raw: str | None, mode: str) -> _SessionCursor | None:
    if raw is None:
        return None
    try:
        if not raw or len(raw) > 1024:
            raise ValueError("Invalid cursor")
        value = _SessionCursor.model_validate_json(base64.b64decode(raw, altchars=b"-_", validate=True))
        if (
            value.mode != mode
            or (mode == "messages" and value.message_count is None)
            or (mode != "messages" and value.order_time is None)
        ):
            raise ValueError("Cursor sort does not match")
        return value
    except (ValueError, TypeError) as exc:
        raise RequestError(status_code=422, message="会话分页位置无效，请重新加载列表") from exc


def _session_sort(mode: str, message_count: ColumnElement | None = None) -> list[tuple[ColumnElement, bool]]:
    if mode == "archived":
        return [(Conversation.archived_at, True), (Conversation.id, False)]
    metric = (
        message_count
        if mode == "messages"
        else Conversation.created_at
        if mode == "created"
        else Conversation.updated_at
    )
    if metric is None:
        raise ValueError("Message order requires statistics")
    return [
        (case((Conversation.kind == SPECIAL_KIND, 1), else_=0), True),
        (case((Conversation.pinned_at.is_not(None), 1), else_=0), True),
        (
            func.coalesce(Conversation.pinned_at, literal(datetime(1970, 1, 1, tzinfo=UTC), DateTime(timezone=True))),
            True,
        ),
        (metric, True),
        (Conversation.id, False),
    ]


def _after_session_cursor(ordering: list[tuple[ColumnElement, bool]], cursor: _SessionCursor) -> ColumnElement[bool]:
    values: list[Any] = (
        [cursor.order_time, cursor.identifier]
        if cursor.mode == "archived"
        else [
            int(cursor.system),
            int(cursor.pinned_at is not None),
            cursor.pinned_at or datetime(1970, 1, 1, tzinfo=UTC),
            cursor.message_count if cursor.mode == "messages" else cursor.order_time,
            cursor.identifier,
        ]
    )
    equal: list[ColumnElement[bool]] = []
    alternatives = []
    for (column, descending), value in zip(ordering, values, strict=True):
        alternatives.append(and_(*equal, column < value if descending else column > value))
        equal.append(column == value)
    return or_(*alternatives)


def _encode_session_cursor(conv: Conversation, mode: str, message_count: int) -> str:
    value = _SessionCursor(
        mode=mode,
        identifier=conv.id,
        system=conv.kind == SPECIAL_KIND,
        pinned_at=conv.pinned_at,
        order_time=conv.archived_at
        if mode == "archived"
        else conv.created_at
        if mode == "created"
        else conv.updated_at
        if mode == "recent"
        else None,
        message_count=message_count if mode == "messages" else None,
    )
    return base64.urlsafe_b64encode(value.model_dump_json().encode()).decode()


async def list_sessions(
    user_id: int,
    db: AsyncSession,
    limit: int = 40,
    cursor: str | None = None,
    min_messages: int = 0,
    archived: Literal["only", "exclude", "include"] = "exclude",
    order: Literal["recent", "created", "messages"] = "recent",
    include_subagents: bool = False,
    query: str | None = None,
    companion_name: str | None = None,
    include_total: bool = False,
) -> DesktopSessionListResponse:
    conditions = [Conversation.user_id == user_id]
    if archived == "only":
        conditions.append(Conversation.archived_at.is_not(None))
    elif archived == "exclude":
        conditions.append(Conversation.archived_at.is_(None))
        if not include_subagents:
            conditions.append(Conversation.parent_id.is_(None))
    if query:
        query = query[:SEARCH_INPUT_MAX_LEN]
        escaped = (
            query.replace(SQL_LIKE_ESCAPE_CHAR, SQL_LIKE_ESCAPE_CHAR * 2)
            .replace("%", f"{SQL_LIKE_ESCAPE_CHAR}%")
            .replace("_", f"{SQL_LIKE_ESCAPE_CHAR}_")
        )
        pattern = f"%{escaped}%"
        display_title = case(
            (Conversation.kind == SPECIAL_KIND, literal(companion_name, String())), else_=Conversation.title
        )
        content_ids = (
            select(Message.conversation_id)
            .where(Message.user_id == user_id, message_contains_text(query))
            .distinct()
            .correlate(None)
        )
        conditions.append(
            or_(
                display_title.ilike(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
                cast(Conversation.id, String).like(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
                Conversation.id.in_(content_ids),
            )
        )
    mode = "archived" if archived == "only" else order
    position = _session_cursor(cursor, mode)
    if min_messages > 0:
        conditions.append(Conversation.visible_message_count >= literal(min_messages, BigInteger()))
    selection = select(Conversation.id).where(*conditions)
    total = int(await db.scalar(select(func.count()).select_from(selection.subquery())) or 0) if include_total else None
    ordering = _session_sort(mode, Conversation.visible_message_count)
    if position is not None:
        selection = selection.where(_after_session_cursor(ordering, position))
    page = (
        selection.order_by(*(column.desc() if descending else column.asc() for column, descending in ordering))
        .limit(limit + 1)
        .cte("session_page")
    )
    statement = (
        select(
            Conversation,
            _preview_subquery,
            Conversation.visible_message_count.label("msg_count"),
            Conversation.input_token_count,
            Conversation.output_token_count,
            Conversation.tool_call_count,
        )
        .join(page, Conversation.id == page.c.id)
        .order_by(*(column.desc() if descending else column.asc() for column, descending in ordering))
    )
    rows = list((await db.execute(statement)).all())
    more = len(rows) > limit
    rows = rows[:limit]
    sessions = [
        _conversation_to_session_info(
            conv,
            msg_count=int(mc or 0),
            input_tok=int(it or 0),
            output_tok=int(ot or 0),
            tool_count=int(tc or 0),
            preview=preview,
            companion_name=companion_name,
        )
        for conv, preview, mc, it, ot, tc in rows
    ]
    next_cursor = _encode_session_cursor(rows[-1][0], mode, int(rows[-1].msg_count or 0)) if more else None
    return DesktopSessionListResponse(
        limit=limit, total=total, sessions=sessions, has_more=more, next_cursor=next_cursor
    )


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
        settings = dict(conv.settings_json or {})
        for key, value in body.settings.model_dump(exclude_unset=True).items():
            if value is None:
                settings.pop(key, None)
            else:
                settings[key] = value
        conv.settings_json = settings
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
