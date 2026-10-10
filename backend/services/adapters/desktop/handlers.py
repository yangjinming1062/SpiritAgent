import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, TypeGuard
from uuid import UUID

from components import (
    ATTACHMENT_DATA_URL_MAX_CHARS,
    ATTACHMENT_TYPE_IMAGE,
    ATTACHMENT_TYPE_VIDEO,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_SLASH_BUSY,
    JSONRPC_SLASH_CONFIRM_REQUIRED,
    JSONRPC_SLASH_GENERIC,
    JSONRPC_TURN_BUSY,
    MAX_VOICE_DESIGN_PROMPT_CHARS,
    REQUEST_ID_HEADER,
    SESSION_HISTORY_PRE_BUFFER,
    SESSION_HISTORY_TRUNCATE_THRESHOLD,
    SESSION_LOCAL,
    SETTINGS,
    adopt_inbound,
    attachments_gc_session,
    coerce_hour_0_23,
    coerce_non_negative_float,
    get_logger,
    is_user_in_maintenance,
)
from fastapi import WebSocket, WebSocketDisconnect
from modules.auth import ChatRequestClientContext, is_remote_session_active
from modules.companion import ActionPlayRequest, AvatarGenerateRequest, CompanionSignal
from modules.conversation import Conversation, Message
from modules.remote import PromptSubmission, consume_remote_ws_ticket
from modules.settings import load_user_settings, record_user_timezone
from modules.system import (
    ChatAttachment,
    ChatMessageRequest,
    ChatRequest,
    ImageAttachResponse,
    PromptPresetListResponse,
    PromptPresetSummary,
)
from modules.ws import COMPANION_TURN_EVENT
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.actions import choose_idle_desktop_action, request_playback
from services.application.chat import (
    CompressionFailedError,
    SubmissionConflictError,
    compress_session_history,
    find_submission,
    merge_session_settings,
    persist_extra_user_messages,
    persist_submission_input,
    record_submission_state,
    reserve_submission,
    resolve_inference_settings,
    run_chat_turn,
)
from services.application.generation import (
    AvatarGenerationError,
    avatar_response,
    cleanup_user_media,
    get_avatar_job_lock,
    raise_if_image_sealed,
    regenerate_avatar,
)
from services.contracts import EmbeddingItem, MemoryScope, MemorySource
from services.domains.assets import collect_message_asset_releases
from services.domains.companion import (
    PersonaValidationError,
    check_idle_expression,
    design_voice,
    emit_companion_message,
    get_disturbance_tier,
    get_onboarding_state,
    get_or_create_persona,
    get_presentation_snapshot,
    invalidate_user_interaction_stats,
    list_tts_voices,
    match_user_voice,
    normalize_voice_language,
    observe_companion_presence,
    queue_companion_intent,
    record_interaction,
    should_act,
    submit_onboarding_field,
    user_turn_activity,
)
from services.domains.conversation import (
    CLEARED_STATUS_SUBTYPE,
    COMPANION_PRESET_ID,
    SPECIAL_KIND,
    SYSTEM_PRESET_CATALOG,
    EditNotAllowedError,
    ForkNotAllowedError,
    ReplyRetryNotAllowedError,
    SourceNotFoundError,
    UndoNotAllowedError,
    build_session_messages,
    cancel_reply_audio,
    conversation_memory_scope,
    count_conversation_bubbles,
    fork_conversation_from_message,
    get_or_create_special_conversation,
    get_reply_retry_message,
    message_text,
    replace_last_user_message,
    resolve_memory_scope,
    resolve_undo_target,
    undo_conversation_to_message,
    validate_memory_scope,
)
from services.domains.media import (
    copy_forked_video_attachments,
    prune_videos_in_range,
    resolve_video_file,
    video_file_id_from_url,
)
from services.domains.memory import (
    backfill_memory_embeddings,
    create_memory,
    delete_memory,
    list_memories,
    memory_counts,
    normalize_recall_context,
    update_memory,
)
from services.infrastructure.assets import user_asset_lock
from services.infrastructure.desktop import (
    MANAGER,
    JsonRpcDispatcher,
    JsonRpcError,
    UserEventPublisher,
    discard_user,
    resolve_future,
)
from services.infrastructure.event_store import interrupt_user_event_tasks
from services.infrastructure.llm import (
    MissingLlmConfigError,
    UserLlmConfig,
    resolve_user_llm_config,
)
from services.infrastructure.tool_runtime import REGISTRY
from services.infrastructure.turn_ownership import conversation_lock

from .auth import decode_ws_ticket, is_ws_login_active
from .emitter import JsonRpcEmitter
from .runtime import (
    ActiveTurnSnapshot,
    LastSubmissionSnapshot,
    RuntimeSession,
    SessionCreateResult,
    SessionResumeResult,
    SessionRuntimeInfo,
    SessionSettingsPatch,
    ToolsSyncResult,
    build_runtime_info,
    decode_session_settings,
    new_runtime_session,
)
from .slash_commands import (
    SlashCommandContext,
    SlashCommandResult,
    list_commands_for_user,
    register_slash_command,
    resolve_slash_command,
    suggest_commands,
)

logger = get_logger(__name__)


@dataclass
class AccountChatRuntime:
    user_id: int
    runtime_sessions: dict[str, RuntimeSession] = field(default_factory=dict)
    background_tasks: set[asyncio.Task] = field(default_factory=set)

    def track(self, task: asyncio.Task) -> None:
        self.background_tasks.add(task)

        def finished(done: asyncio.Task) -> None:
            self.background_tasks.discard(done)
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.warning("account background task failed", extra={"user_id": self.user_id}, exc_info=error)
            _release_account_runtime(self.user_id)

        task.add_done_callback(finished)


@dataclass
class UserGatewaySession:
    user_id: int
    login_record_id: int
    dispatcher: JsonRpcDispatcher
    session_client_context: ChatRequestClientContext | None
    account: AccountChatRuntime
    device_id: int | None = None
    connection_id: str = "desktop"
    websocket: WebSocket | None = None
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    grace_timer_task: asyncio.Task | None = None

    @property
    def runtime_sessions(self) -> dict[str, RuntimeSession]:
        return self.account.runtime_sessions

    @property
    def origin_kind(self) -> str:
        return "remote" if self.device_id is not None else "desktop"

    @property
    def origin_id(self) -> str:
        return str(self.device_id if self.device_id is not None else self.login_record_id)

    async def authorized(self) -> bool:
        if is_user_in_maintenance(self.user_id):
            return False
        if self.device_id is not None:
            return await is_remote_session_active(self.user_id, self.device_id)
        return await is_ws_login_active(self.user_id, self.login_record_id)

    def track(self, task: asyncio.Task) -> None:
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)


_ACCOUNT_RUNTIMES: dict[int, AccountChatRuntime] = {}
_USER_SESSIONS: dict[int, UserGatewaySession] = {}
_REMOTE_SESSIONS: dict[int, dict[str, UserGatewaySession]] = {}
_GRACE_TIMER_TASKS: set[asyncio.Task] = set()
_USER_LOCKS: dict[int, asyncio.Lock] = {}


def _user_lock(user_id: int) -> asyncio.Lock:
    return _USER_LOCKS.setdefault(user_id, asyncio.Lock())


def _release_account_runtime(user_id: int) -> None:
    account = _ACCOUNT_RUNTIMES.get(user_id)
    if account is None or user_id in _USER_SESSIONS or _REMOTE_SESSIONS.get(user_id):
        return
    if account.background_tasks or any(runtime.busy for runtime in account.runtime_sessions.values()):
        return
    _ACCOUNT_RUNTIMES.pop(user_id, None)


async def _cancel_tasks(tasks: list[asyncio.Task | None]) -> None:
    pending = [task for task in tasks if task is not None and not task.done() and task is not asyncio.current_task()]
    for task in pending:
        if not task.cancelling():
            task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _cancel_origin_turns(
    user_id: int,
    origin_kind: str,
    origin_id: str | None,
    *,
    preserve_companion_turns: bool = False,
) -> None:
    account = _ACCOUNT_RUNTIMES.get(user_id)
    if account is None:
        return
    runtimes = [
        runtime
        for runtime in account.runtime_sessions.values()
        if runtime.origin_kind == origin_kind
        and (origin_id is None or runtime.origin_id == origin_id)
        and not (
            preserve_companion_turns
            and runtime.kind == SPECIAL_KIND
            and runtime.system_preset_id == COMPANION_PRESET_ID
        )
    ]
    await _cancel_tasks(
        [runtime.chat_task for runtime in runtimes if runtime.active_turn is None or runtime.active_turn.running],
    )
    await _wait_turn_settlement(runtimes)


async def _wait_turn_settlement(runtimes: list[RuntimeSession]) -> None:
    # 主任务的完成回调可能创建收尾任务；分两轮等待，不再取消持久状态回写。
    for settling in (False, True):
        tasks = [runtime.settlement_task if settling else runtime.chat_task for runtime in runtimes]
        pending = [task for task in tasks if task is not None and task is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def drain() -> None:
    runtimes = [runtime for account in _ACCOUNT_RUNTIMES.values() for runtime in account.runtime_sessions.values()]
    settling = {
        task
        for runtime in runtimes
        for task in (
            runtime.settlement_task,
            runtime.chat_task if runtime.active_turn is not None and not runtime.active_turn.running else None,
        )
        if task is not None
    }
    tasks: list[asyncio.Task | None] = list(_GRACE_TIMER_TASKS)
    for account in _ACCOUNT_RUNTIMES.values():
        tasks.extend(task for task in account.background_tasks if task not in settling)
    for session in [
        *_USER_SESSIONS.values(),
        *(peer for peers in _REMOTE_SESSIONS.values() for peer in peers.values()),
    ]:
        tasks.extend(session.background_tasks)
    await _cancel_tasks(tasks)
    await _wait_turn_settlement(runtimes)
    for user_id in list(_REMOTE_SESSIONS):
        await terminate_remote_sessions(user_id)


async def _noop_send(data: dict[str, Any]) -> bool:
    return False


_last_idle_expression_ts: dict[int, float] = {}
SHOULD_ACT_ANTIDUP_SECONDS = 2.0
_last_should_act_ts: dict[int, float] = {}
_VOICE_PREVIEW_TEXT_MAX_CHARS = MAX_VOICE_DESIGN_PROMPT_CHARS


@contextlib.asynccontextmanager
async def desktop_history_lock(session_id: str) -> AsyncIterator[None]:
    lock = conversation_lock(session_id)
    if lock.locked():
        raise JsonRpcError(JSONRPC_TURN_BUSY, "当前会话正在执行任务，请先停止任务")
    async with lock:
        yield


async def _terminate_user_gateway_locked(
    user_id: int,
    login_record_id: int | None = None,
    *,
    preserve_companion_turns: bool = False,
) -> None:
    session = _USER_SESSIONS.get(user_id)
    if session is None or (login_record_id is not None and session.login_record_id != login_record_id):
        # 断线后网关可能已释放，撤权仍需取消账户持有的来源回合。
        if not preserve_companion_turns:
            await _cancel_origin_turns(
                user_id,
                "desktop",
                str(login_record_id) if login_record_id is not None else None,
            )
            _release_account_runtime(user_id)
        return
    _USER_SESSIONS.pop(user_id, None)
    if (websocket := MANAGER.active_connections.get(user_id)) is not None:
        with contextlib.suppress(Exception):
            await websocket.close(code=1008)
        MANAGER.disconnect(websocket, user_id)
    observe_companion_presence(user_id, False, presentation_mode=None)
    await _cancel_origin_turns(
        user_id,
        "desktop",
        str(session.login_record_id),
        preserve_companion_turns=preserve_companion_turns,
    )
    await _cancel_tasks([session.grace_timer_task, *session.background_tasks])
    await interrupt_user_event_tasks(user_id, COMPANION_TURN_EVENT)
    await MANAGER.aunregister_dispatcher(user_id)
    REGISTRY.clear_runner_tools(user_id)
    discard_user(user_id)
    _last_idle_expression_ts.pop(user_id, None)
    _last_should_act_ts.pop(user_id, None)
    _release_account_runtime(user_id)


async def terminate_user_gateway(user_id: int, *, login_record_id: int | None = None) -> None:
    async with _user_lock(user_id):
        await _terminate_user_gateway_locked(user_id, login_record_id)


async def terminate_remote_sessions(user_id: int, device_id: int | None = None) -> None:
    async with _user_lock(user_id):
        for connection_id, session in list(_REMOTE_SESSIONS.get(user_id, {}).items()):
            if device_id is not None and session.device_id != device_id:
                continue
            _REMOTE_SESSIONS[user_id].pop(connection_id, None)
            if session.websocket is not None:
                with contextlib.suppress(Exception):
                    await session.websocket.close(code=1008)
            await _cancel_tasks(list(session.background_tasks))
            await MANAGER.unregister_remote(user_id, connection_id)
        if not _REMOTE_SESSIONS.get(user_id):
            _REMOTE_SESSIONS.pop(user_id, None)
        await _cancel_origin_turns(user_id, "remote", str(device_id) if device_id is not None else None)
        _release_account_runtime(user_id)


async def _expire_disconnected_gateway(user_id: int) -> None:
    try:
        await asyncio.sleep(SETTINGS.desktop_disconnect_grace_seconds)
        async with _user_lock(user_id):
            if not MANAGER.is_connected(user_id):
                await _terminate_user_gateway_locked(user_id, preserve_companion_turns=True)
    except asyncio.CancelledError:
        pass


def _user_throttled(state: dict[int, float], user_id: int, min_interval: float, now: float) -> bool:
    """若用户仍在窗口内返回 True 且不更新时间戳。"""
    return now - state.get(user_id, 0.0) < min_interval


def _ws_sender(websocket: WebSocket) -> Callable[[dict[str, Any]], Awaitable[bool]]:
    async def send(data: dict[str, Any]) -> bool:
        try:
            async with asyncio.timeout(15):
                await websocket.send_json(data)
            return True
        except WebSocketDisconnect:
            return False
        except RuntimeError as e:
            if "close message" not in str(e):
                logger.debug("websocket send RuntimeError", extra={"error": str(e)})
            return False
        except Exception as e:
            logger.debug("websocket send unexpected", extra={"error": str(e)}, exc_info=True)
            return False

    return send


async def _read_socket(websocket: WebSocket, session: UserGatewaySession) -> None:
    while True:
        data = await websocket.receive_text()
        async with _user_lock(session.user_id):
            current = (
                _REMOTE_SESSIONS.get(session.user_id, {}).get(session.connection_id) is session
                if session.device_id is not None
                else _USER_SESSIONS.get(session.user_id) is session
                and MANAGER.active_connections.get(session.user_id) is websocket
            )
            if not current:
                return
            valid = await session.authorized()
            if valid:
                dispatch_task = asyncio.create_task(session.dispatcher.handle_raw(data))
                session.track(dispatch_task)
        if not valid:
            if session.device_id is not None:
                await terminate_remote_sessions(session.user_id, session.device_id)
            else:
                await terminate_user_gateway(session.user_id, login_record_id=session.login_record_id)
            return
        try:
            await dispatch_task
        except asyncio.CancelledError:
            return


async def handle_chat_websocket(websocket: WebSocket, token: str) -> None:
    adopt_inbound(websocket.headers.get(REQUEST_ID_HEADER))
    ticket = decode_ws_ticket(token)
    if ticket is None:
        await websocket.close(code=1008)
        return
    user_id = ticket.user_id
    async with _user_lock(user_id):
        if is_user_in_maintenance(user_id) or not await is_ws_login_active(user_id, ticket.login_record_id):
            await websocket.close(code=1008)
            return
        session = _USER_SESSIONS.get(user_id)
        if session is not None and (session.login_record_id != ticket.login_record_id or MANAGER.is_connected(user_id)):
            await _terminate_user_gateway_locked(
                user_id,
                preserve_companion_turns=session.login_record_id == ticket.login_record_id,
            )
            session = None
        try:
            await MANAGER.connect(websocket, user_id)
            async with SESSION_LOCAL() as db:
                await get_or_create_special_conversation(db, user_id, "companion")
            if session is None:
                account = _ACCOUNT_RUNTIMES.setdefault(user_id, AccountChatRuntime(user_id))
                session = UserGatewaySession(
                    user_id=user_id,
                    login_record_id=ticket.login_record_id,
                    dispatcher=JsonRpcDispatcher(_ws_sender(websocket)),
                    session_client_context=ticket.client_context,
                    account=account,
                    websocket=websocket,
                )
                _USER_SESSIONS[user_id] = session
                _register_session_handlers(session)
            else:
                if session.grace_timer_task is not None:
                    session.grace_timer_task.cancel()
                    session.grace_timer_task = None
                session.dispatcher.set_sender(_ws_sender(websocket))
                session.session_client_context = ticket.client_context
                session.websocket = websocket
            session.dispatcher.enable_hold()
            MANAGER.register_dispatcher(user_id, session.dispatcher)
        except BaseException:
            logger.exception("desktop websocket initialization failed", extra={"user_id": user_id})
            await _terminate_user_gateway_locked(user_id, preserve_companion_turns=True)
            MANAGER.disconnect(websocket, user_id)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011)
            raise
    try:
        await _read_socket(websocket, session)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if MANAGER.active_connections.get(user_id) is websocket:
            MANAGER.disconnect(websocket, user_id)
            observe_companion_presence(user_id, False, presentation_mode=None)
            session.dispatcher.set_sender(_noop_send)
            task = asyncio.create_task(_expire_disconnected_gateway(user_id))
            _GRACE_TIMER_TASKS.add(task)
            task.add_done_callback(_GRACE_TIMER_TASKS.discard)
            session.grace_timer_task = task


async def handle_remote_websocket(websocket: WebSocket, token: str) -> None:
    adopt_inbound(websocket.headers.get(REQUEST_ID_HEADER))
    identity = consume_remote_ws_ticket(token)
    if identity is None:
        await websocket.close(code=1008)
        return
    user_id = identity.user_id
    session: UserGatewaySession | None = None
    async with _user_lock(user_id):
        if is_user_in_maintenance(user_id) or not await is_remote_session_active(user_id, identity.device_id):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            async with SESSION_LOCAL() as db:
                await get_or_create_special_conversation(db, user_id, "companion")
            account = _ACCOUNT_RUNTIMES.setdefault(user_id, AccountChatRuntime(user_id))
            connection_id = secrets.token_hex(16)
            session = UserGatewaySession(
                user_id=user_id,
                login_record_id=0,
                device_id=identity.device_id,
                dispatcher=JsonRpcDispatcher(_ws_sender(websocket)),
                account=account,
                session_client_context=ChatRequestClientContext(platform_hints="SpiritAgentRemote mobile browser"),
                connection_id=connection_id,
                websocket=websocket,
            )
            _REMOTE_SESSIONS.setdefault(user_id, {})[connection_id] = session
            _register_session_handlers(session)
            session.dispatcher.enable_hold()
            MANAGER.register_remote(user_id, connection_id, session.dispatcher)
        except BaseException:
            if session is not None:
                _REMOTE_SESSIONS.get(user_id, {}).pop(session.connection_id, None)
                await MANAGER.unregister_remote(user_id, session.connection_id)
            _release_account_runtime(user_id)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011)
            raise
    try:
        await _read_socket(websocket, session)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _REMOTE_SESSIONS.get(user_id, {}).pop(session.connection_id, None)
        if not _REMOTE_SESSIONS.get(user_id):
            _REMOTE_SESSIONS.pop(user_id, None)
        await MANAGER.unregister_remote(user_id, session.connection_id)
        await _cancel_tasks(list(session.background_tasks))
        _release_account_runtime(user_id)


async def _require_owned_conv(db: AsyncSession, user_id: int, session_id: str) -> Conversation:
    """把 renderer 给的 session_id（DB 主键）解析为该用户的 Conversation；不存在或不属于该用户时抛 METHOD_NOT_FOUND。"""
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)
    if conv is None:
        raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {session_id!r}")
    return conv


async def _resolve_llm_config(user_id: int) -> UserLlmConfig:
    """每次调用现读用户能力链，管理端改配置后已连接的桌面无需重连即生效。"""
    async with SESSION_LOCAL() as db:
        return await resolve_user_llm_config(db, user_id)


def _reject_read_only_session(runtime: RuntimeSession) -> None:
    """渠道和自动化历史只由各自执行入口写入。"""
    if runtime.is_automation:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "任务会话由对应执行入口维护，仅只读")


def _require_str(params: dict[str, Any], key: str) -> str:
    v = params.get(key)
    if not isinstance(v, str):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a string")
    return v


def _optional_str(params: dict[str, Any], key: str) -> str | None:
    v = params.get(key)
    if v is not None and not isinstance(v, str):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a string")
    return v


def _is_nonneg_int(v: object) -> TypeGuard[int]:
    """type(v) is int 拒绝 bool（Python 把 bool 当 int 子类）。"""
    return type(v) is int and v >= 0


def _require_nonneg_int(params: dict[str, Any], key: str) -> int:
    v = params.get(key)
    if not _is_nonneg_int(v):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a non-negative int")
    return v


def _session_video_file_id(file_url: str, session_id: str) -> str | None:
    """视频附件只认本会话的后端上传 URL（相对路径或 public_base_url 前缀），返回其 file_id；任意第三方绝对 URL 会让供应商替我们发任意请求，必须绑死前缀。"""
    return video_file_id_from_url(file_url, session_id) if len(file_url) <= 2048 else None


def _validate_attachments(params: dict[str, Any], session_id: str) -> list[ChatAttachment] | None:
    """校验并规范化 attachments（每项重塑为 {type, file_url}），未传时返回 None。image 接受 HTTP(S) 与 data:image URL；video 只认本会话后端上传且文件仍在的 URL（base64 视频超 WS 单帧上限，须先 POST /api/media/videos）。"""
    raw = params.get("attachments")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "attachments must be a list")
    if len(raw) > SETTINGS.max_attachments_per_turn:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"too many attachments (max {SETTINGS.max_attachments_per_turn})")
    cleaned: list[ChatAttachment] = []
    for idx, att in enumerate(raw):
        if not isinstance(att, dict):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}] must be an object")
        att_type = att.get("type", ATTACHMENT_TYPE_IMAGE)
        if att_type not in (ATTACHMENT_TYPE_IMAGE, ATTACHMENT_TYPE_VIDEO):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}].type must be image or video")

        file_url = att.get("file_url")
        if not (file_url and isinstance(file_url, str)):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}] must have file_url")

        if att_type == ATTACHMENT_TYPE_VIDEO:
            if file_url.startswith("data:"):
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"attachments[{idx}] video must reference an uploaded URL, not a data URL",
                )
            file_id = _session_video_file_id(file_url, session_id)
            if file_id is None:
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"attachments[{idx}].file_url must be a video URL uploaded to this session (/api/media/videos/{session_id}/...)",
                )
            # 文件可能已被配额剔除或清理；拒绝提交，不把死链写入历史或交给供应商。
            if resolve_video_file(session_id, file_id) is None:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "视频附件已失效，请重新上传")
        elif file_url.startswith("http"):
            # HTTP/HTTPS URL（长度与 URL 语义一致，维持紧上限）
            if len(file_url) > 2048:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}].file_url too long")
        elif file_url.startswith("data:image/"):
            # 桌面端本地图片 data URL：字节在负载内，不落盘；字符上限覆盖 base64 膨胀。
            if len(file_url) > ATTACHMENT_DATA_URL_MAX_CHARS:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}].file_url too long")
        else:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}] must have file_url")
        cleaned.append(ChatAttachment(type=att_type, file_url=file_url))
    return cleaned


async def _discard_forked_conversation(db: AsyncSession, conv_id: int) -> None:
    """派生后续步骤失败时撤销新会话：删除会话行（消息级联）和已写入的附件目录，不留下半成品。"""
    await db.rollback()
    conv = await db.get(Conversation, conv_id)
    if conv is None:
        return
    async with user_asset_lock(conv.user_id):
        removed = await collect_message_asset_releases(db, conv.user_id, [conv_id])
        await db.execute(delete(Conversation).where(Conversation.id == conv_id))
        await db.commit()
    await cancel_reply_audio(conv.user_id, removed)
    await cleanup_user_media(conv.user_id)
    try:
        await asyncio.to_thread(attachments_gc_session, str(conv_id))
    except OSError:
        logger.warning("attachments_gc_session failed for forked session %s", conv_id, exc_info=True)


async def _do_compress_history(conv: Conversation, user_id: int, runtime: RuntimeSession) -> dict[str, Any]:
    """session.compress_context 与 /压缩 命令的共用实现；调用方持会话锁并已校验在途回合。compressed=False 时返回体不含 messages/summary，True 时含 delivered messages 给前端 hydrate；摘要失败抛 JSON-RPC 错误，不当作无需压缩。"""
    gateway = _USER_SESSIONS.get(user_id)
    client_context = gateway.session_client_context if gateway is not None else None
    try:
        result = await compress_session_history(conv, user_id, runtime.settings, client_context)
    except MissingLlmConfigError as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, "模型服务暂未配置，请联系管理员") from exc
    except CompressionFailedError as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, "上下文压缩失败，请稍后重试") from exc
    usage = {"total_tokens": result.total_tokens, "context_window": result.context_window}
    if result.info is None:
        return {
            "session_id": runtime.session_id,
            "compressed": False,
            "reason": "历史消息较少，无需压缩（至少需要保留最近对话）",
            "usage": usage,
        }
    async with SESSION_LOCAL() as db:
        delivered = await build_session_messages(conv.id, db)
    return {
        "session_id": runtime.session_id,
        "compressed": True,
        "replaced_count": result.info.replaced_count,
        "summary": result.info.summary,
        "messages": delivered,
        "usage": usage,
    }


async def _do_clear_history(db: AsyncSession, conv: Conversation) -> dict[str, Any]:
    """清空会话所有消息（含各 role，保留会话行 + 写一条 status_cleared 标记）；返回 {"session_id", "cleared_count", "messages"}，cleared_count 为真实删除行数。"""
    total = (
        await db.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conv.id),
        )
    ).scalar_one()

    # 临时上传视频的独立提交先完成，不能提前提交正式资源回收登记。
    await prune_videos_in_range(db, conv.id)
    # 删除业务引用与回收登记同事务；提交以后再取消任务和删除文件。
    async with user_asset_lock(conv.user_id):
        removed = await collect_message_asset_releases(db, conv.user_id, [conv.id])
        await db.execute(delete(Message).where(Message.conversation_id == conv.id))
        marker = Message(
            conversation_id=conv.id,
            role="system",
            content=f"[🧹 会话已清空 — {total} 条消息]",
            subtype=CLEARED_STATUS_SUBTYPE,
        )
        db.add(marker)
        await db.commit()
    await cancel_reply_audio(conv.user_id, removed)
    await cleanup_user_media(conv.user_id)

    delivered = await build_session_messages(conv.id, db)
    return {
        "session_id": str(conv.id),
        "cleared_count": total,
        "messages": delivered,
    }


@register_slash_command(
    name="clear",
    aliases=("清空", "清理", "reset"),
    description="清空当前会话消息（保留会话行）",
    requires_confirmation=True,
)
async def _slash_clear(ctx: SlashCommandContext) -> SlashCommandResult:
    """``/清理`` 命令 handler。``confirmed`` 由 ``command.dispatch`` 在调用前把关，未传则抛 SLASH_CONFIRM_REQUIRED。"""
    _reject_read_only_session(ctx.runtime)
    async with desktop_history_lock(ctx.session_id), SESSION_LOCAL() as db:
        if ctx.runtime.busy:
            raise JsonRpcError(JSONRPC_SLASH_BUSY, "请先停止当前生成再清理会话")
        conv = await _require_owned_conv(db, ctx.user_id, ctx.session_id)
        result = await _do_clear_history(db, conv)
    cleared = result["cleared_count"]
    if cleared == 0:
        return SlashCommandResult(
            status="ok",
            message="当前会话无消息可清空",
            hydrate=True,
            payload=result,
        )
    return SlashCommandResult(
        status="ok",
        message=f"已清空 {cleared} 条消息",
        hydrate=True,
        payload=result,
    )


@register_slash_command(
    name="compress",
    aliases=("压缩", "ctx"),
    description="压缩当前会话上下文",
    requires_confirmation=False,
)
async def _slash_compress(ctx: SlashCommandContext) -> SlashCommandResult:
    """``/压缩`` 命令 handler：复用 session.compress_context 的核心实现。"""
    _reject_read_only_session(ctx.runtime)
    async with desktop_history_lock(ctx.session_id):
        if ctx.runtime.busy:
            raise JsonRpcError(JSONRPC_SLASH_BUSY, "请先停止当前生成再压缩会话")
        async with SESSION_LOCAL() as db:
            conv = await _require_owned_conv(db, ctx.user_id, ctx.session_id)
        try:
            result = await _do_compress_history(conv, ctx.user_id, ctx.runtime)
        except JsonRpcError as exc:
            return SlashCommandResult(status="error", message=exc.message, hydrate=False)
    if not result["compressed"]:
        return SlashCommandResult(
            status="ok",
            message="历史消息较少，无需压缩",
            hydrate=False,
            payload=result,
        )
    return SlashCommandResult(
        status="ok",
        message=f"已压缩 {result['replaced_count']} 条早期消息",
        hydrate=True,
        payload=result,
    )


@register_slash_command(
    name="remember",
    aliases=("记住", "记忆", "remind", "memo", "memory"),
    description="主动记住指定内容到长期记忆",
    requires_confirmation=False,
)
async def _slash_remember(ctx: SlashCommandContext) -> SlashCommandResult:
    """``/记住`` 命令 handler：主动将指定内容持久化写入长期记忆并触发向量化。"""
    content = " ".join(ctx.args).strip()
    if not content:
        return SlashCommandResult(
            status="ok",
            message="请提供要记住的内容，例如：/记住 我喜欢喝无糖乌龙茶",
            hydrate=False,
        )

    max_chars = SETTINGS.memory_recall_max_content_chars
    if len(content) > max_chars:
        return SlashCommandResult(status="error", message=f"要记住的内容最多 {max_chars} 个字符，请精简后再试")
    context = normalize_recall_context("manual")
    tags = json.dumps(["user_preference"])
    importance = 1.5

    async with SESSION_LOCAL() as db:
        conv = await _require_owned_conv(db, ctx.user_id, ctx.session_id)
        scope = conversation_memory_scope(conv, ctx.user_id)
        if scope is None:
            return SlashCommandResult(status="error", message="定时任务会话不保存长期记忆，无法记住内容")
        mem = await create_memory(
            db,
            scope,
            source=MemorySource("manual", session_id=int(ctx.session_id)),
            content=content,
            context=context,
            tags=tags,
            importance=importance,
        )
        await db.commit()

    await backfill_memory_embeddings(scope, [EmbeddingItem(mem.id, mem.content, mem.content_version)])

    display_content = content if len(content) <= 60 else f"{content[:57]}..."
    return SlashCommandResult(
        status="ok",
        message=f"已记住：{display_content}",
        hydrate=False,
        payload={"memory_id": mem.id, "content": content},
    )


async def _fetch_truncated_history(conv_id: int, db: AsyncSession) -> tuple[list[dict[str, Any]], bool, str | None]:
    head = await build_session_messages(
        conv_id,
        db,
        latest=SESSION_HISTORY_TRUNCATE_THRESHOLD + SESSION_HISTORY_PRE_BUFFER,
    )
    truncated = len(head) > SESSION_HISTORY_TRUNCATE_THRESHOLD
    delivered = head[-SESSION_HISTORY_TRUNCATE_THRESHOLD:] if truncated else head
    next_cursor = str(delivered[0]["id"]) if truncated and delivered else None
    return delivered, truncated, next_cursor


def _register_session_handlers(session: UserGatewaySession) -> None:
    user_id = session.user_id
    dispatcher = session.dispatcher
    runtime_sessions = session.runtime_sessions
    replay_buffer = dispatcher.replay_buffer
    publisher = UserEventPublisher(user_id)
    remote_methods = {
        "session.ack",
        "session.ping",
        "session.get_main",
        "session.create",
        "session.resume",
        "session.history",
        "session.interrupt",
        "session.set_settings",
        "session.compress_context",
        "session.undo_to_message",
        "session.fork",
        "system.list_presets",
        "prompt.submit",
        "command.list",
        "command.dispatch",
        "memory.list",
        "memory.update",
        "memory.delete",
    }

    def register(method: str, handler: Callable[[dict], Awaitable[Any]]) -> None:
        if session.device_id is None or method in remote_methods:
            dispatcher.register(method, handler)

    def _mount_runtime(conv: Conversation) -> RuntimeSession:
        """历史同步复用已有 runtime，不能取消其他视图正在消费的回合。"""
        existing = runtime_sessions.get(str(conv.id))
        if existing is not None:
            return existing
        runtime = new_runtime_session(conv)
        runtime_sessions[runtime.session_id] = runtime
        return runtime

    def _require_runtime(params: dict[str, Any]) -> RuntimeSession:
        session_id = _require_str(params, "session_id")
        runtime = runtime_sessions.get(session_id)
        if runtime is None:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {session_id!r}")
        return runtime

    async def _runtime_info(runtime: RuntimeSession, conv: Conversation) -> SessionRuntimeInfo:
        async with SESSION_LOCAL() as db:
            user_settings = await load_user_settings(db, user_id)
            llm_config = await resolve_user_llm_config(db, user_id)
        effective = merge_session_settings(user_settings, runtime.settings, conv=conv)
        settings = runtime.settings | asdict(resolve_inference_settings(effective, conv=conv))
        return build_runtime_info(llm_config, runtime, settings, system_preset_id=conv.system_preset_id)

    async def _last_submission(runtime: RuntimeSession) -> LastSubmissionSnapshot | None:
        async with SESSION_LOCAL() as db:
            last = await db.scalar(
                select(PromptSubmission)
                .where(PromptSubmission.user_id == user_id, PromptSubmission.conversation_id == runtime.conversation_id)
                .order_by(PromptSubmission.id.desc())
                .limit(1),
            )
            last_submission = None
            if last is not None:
                ids = json.loads(last.message_ids_json)
                retry_id = None
                if last.status == "failed" and ids:
                    try:
                        await get_reply_retry_message(db, user_id, runtime.session_id, ids[-1])
                        retry_id = ids[-1]
                    except ReplyRetryNotAllowedError:
                        pass
                last_submission = LastSubmissionSnapshot(
                    request_id=last.request_id,
                    status=last.status,
                    error=last.error,
                    message_ids=ids,
                    retry_message_id=retry_id,
                )
        return last_submission

    async def _mounted_history(
        conv: Conversation,
        *,
        after_id: int | None = None,
    ) -> dict[str, Any]:
        """在发布锁内读取历史和快照，再确定当前连接的事件水位。"""
        runtime = _mount_runtime(conv)
        async with runtime.snapshot_lock:
            truncated, next_cursor = False, None
            async with SESSION_LOCAL() as db:
                if after_id is not None:
                    messages = await build_session_messages(conv.id, db, after_id=after_id)
                else:
                    messages, truncated, next_cursor = await _fetch_truncated_history(conv.id, db)
                message_count = await count_conversation_bubbles(db, conv.id)
            last_submission = await _last_submission(runtime)
            info = await _runtime_info(runtime, conv)
            snapshot = deepcopy(runtime.active_turn)
            if snapshot is not None and snapshot.message_ids:
                last_input = max(snapshot.message_ids)
                # 终端回复先落库、后逐泡交付；挂载时以持久消息替代其瞬态副本。
                if any(
                    message["role"] == "assistant" and message["id"] > last_input and not message.get("tool_calls")
                    for message in messages
                ):
                    snapshot.text = ""
                    snapshot.reasoning = ""
                    snapshot.bubbles = []
            await dispatcher.flush_unsent()
            return SessionResumeResult(
                session_id=runtime.session_id,
                message_count=message_count,
                messages=messages,
                info=info,
                current_seq=replay_buffer.max_seq,
                stream_id=dispatcher.stream_id,
                active_turn=snapshot,
                last_submission=last_submission,
                truncated=truncated,
                next_cursor=next_cursor,
                incremental=after_id is not None,
            ).model_dump()

    async def publish_history_change(event_type: str, payload: object, session_id: str) -> None:
        runtime = runtime_sessions.get(session_id)
        if runtime is None:
            await publisher.push_event(event_type, payload, session_id=session_id)
            return
        async with runtime.snapshot_lock:
            await publisher.push_event(event_type, payload, session_id=session_id)

    async def session_ack(params: dict) -> dict:
        seq = _require_nonneg_int(params, "seq")
        return {"acked": seq, "pruned": replay_buffer.ack(seq)}

    register("session.ack", session_ack)

    async def session_ping(_params: dict) -> dict:
        return {}

    register("session.ping", session_ping)

    async def session_get_main(_params: dict) -> dict:
        async with SESSION_LOCAL() as db:
            conv = await get_or_create_special_conversation(db, user_id, "companion")
        return await _mounted_history(conv)

    register("session.get_main", session_get_main)

    async def session_create(params: dict) -> dict:
        preset_id = params.get("system_preset_id")
        if not isinstance(preset_id, str) or preset_id not in SYSTEM_PRESET_CATALOG:
            raise JsonRpcError(
                JSONRPC_INVALID_PARAMS,
                f"system_preset_id must be one of {sorted(SYSTEM_PRESET_CATALOG)}",
            )
        async with SESSION_LOCAL() as db:
            conv = Conversation(user_id=user_id, system_preset_id=preset_id)
            db.add(conv)
            await db.commit()
            await db.refresh(conv)
        runtime = _mount_runtime(conv)
        await publisher.push_event("session.list_changed", {"session_id": runtime.session_id})
        logger.info(
            "session.create",
            extra={"user_id": user_id, "session_id": runtime.session_id, "system_preset_id": preset_id},
        )
        await dispatcher.flush_unsent()
        return SessionCreateResult(
            session_id=runtime.session_id,
            info=await _runtime_info(runtime, conv),
        ).model_dump()

    register("session.create", session_create)

    async def system_list_presets(_params: dict) -> dict:
        """返回内置系统预设的元数据清单（不含 body）。body 永远不下发到客户端。"""
        return PromptPresetListResponse(
            presets=[
                PromptPresetSummary(id=p.id, name=p.name, description=p.description, icon_key=p.icon_key)
                for p in SYSTEM_PRESET_CATALOG.values()
            ],
        ).model_dump()

    register("system.list_presets", system_list_presets)

    async def session_fork(params: dict) -> dict:
        source_session_id = _require_str(params, "source_session_id")
        source_message_id = _require_nonneg_int(params, "source_message_id")
        async with SESSION_LOCAL() as db:
            try:
                result = await fork_conversation_from_message(db, user_id, source_session_id, source_message_id)
            except ForkNotAllowedError as e:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(e))
            except SourceNotFoundError as e:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, str(e))
            # 服务函数只负责落库；视频附件复制、runtime 挂载与 info 在这里补，与 session.resume 路径一致
            conv = await _require_owned_conv(db, user_id, result.session_id)
            conv_id = conv.id  # 失败回滚会使 conv 过期，id 须先取出
            try:
                await copy_forked_video_attachments(db, str(conv.forked_from_id), str(conv_id))
            except Exception as exc:
                logger.exception("session.fork video copy failed", extra={"new_session_id": conv_id})
                await _discard_forked_conversation(db, conv_id)
                raise JsonRpcError(JSONRPC_INTERNAL_ERROR, "视频附件复制失败，请稍后重试") from exc
            except asyncio.CancelledError:
                # 网关会话销毁或进程关闭会取消本任务：已提交的派生会话同样撤销，再继续取消。
                await asyncio.shield(_discard_forked_conversation(db, conv_id))
                raise
        logger.info(
            "session.fork",
            extra={
                "user_id": user_id,
                "source_session_id": source_session_id,
                "source_message_id": source_message_id,
                "new_session_id": result.session_id,
                "message_count": result.message_count,
            },
        )
        await publisher.push_event("session.list_changed", {"session_id": str(conv.id)})
        return await _mounted_history(conv)

    register("session.fork", session_fork)

    async def session_resume(params: dict) -> dict:
        stored_id = _require_str(params, "session_id")
        last_seq = params.get("last_seq")
        after_id = params.get("after_id")
        if after_id is not None and not _is_nonneg_int(after_id):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "after_id must be a non-negative int")
        async with SESSION_LOCAL() as db:
            conv = await _require_owned_conv(db, user_id, stored_id)

        # 重放流身份匹配且仍覆盖游标时才重放，否则回源恢复。
        if isinstance(last_seq, int) and last_seq > 0 and params.get("stream_id") == dispatcher.stream_id:
            runtime = _mount_runtime(conv)
            async with runtime.snapshot_lock:
                replayed_count = await dispatcher.replay(last_seq)
                if replayed_count is not None:
                    async with SESSION_LOCAL() as db:
                        message_count = await count_conversation_bubbles(db, conv.id)
                    logger.info(
                        "session.resume replayed frames",
                        extra={
                            "user_id": user_id,
                            "session_id": runtime.session_id,
                            "replayed": replayed_count,
                            "last_seq": last_seq,
                        },
                    )
                    return SessionResumeResult(
                        session_id=runtime.session_id,
                        message_count=message_count,
                        info=await _runtime_info(runtime, conv),
                        resumed=True,
                        replayed_count=replayed_count,
                        current_seq=replay_buffer.max_seq,
                        stream_id=dispatcher.stream_id,
                        last_submission=await _last_submission(runtime),
                        active_turn=deepcopy(runtime.active_turn),
                    ).model_dump()

        # 本地已有历史：锚点仍存在时只回增量，避免冷启动全量重拉。
        if after_id:
            async with SESSION_LOCAL() as db:
                anchor_exists = (
                    await db.execute(
                        select(Message.id).where(Message.id == after_id, Message.conversation_id == conv.id),
                    )
                ).scalar_one_or_none() is not None
            if anchor_exists:
                logger.info(
                    "session.resume incremental",
                    extra={
                        "user_id": user_id,
                        "session_id": stored_id,
                        "after_id": after_id,
                    },
                )
                return await _mounted_history(conv, after_id=after_id)

        # 客户端序列号失同步或超时，回退到 DB 历史防御性截断重水化
        logger.info("session.resume full reload", extra={"user_id": user_id, "session_id": stored_id})
        return await _mounted_history(conv)

    register("session.resume", session_resume)

    async def session_history(params: dict) -> dict:
        stored_id = _require_str(params, "session_id")
        before_id = params.get("before_id")
        limit = params.get("limit", 50)
        if before_id is not None and (not _is_nonneg_int(before_id) or before_id == 0):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "before_id must be a positive int")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "limit must be between 1 and 100")
        async with SESSION_LOCAL() as db:
            conv = await _require_owned_conv(db, user_id, stored_id)
            if (
                before_id is not None
                and await db.scalar(
                    select(Message.id).where(Message.id == before_id, Message.conversation_id == conv.id),
                )
                is None
            ):
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "History cursor is unavailable")
            rows = await build_session_messages(conv.id, db, before_id=before_id, latest=limit + 1)
        has_more = len(rows) > limit
        messages = rows[-limit:]
        return {
            "messages": messages,
            "next_cursor": messages[0]["id"] if has_more and messages else None,
            "has_more": has_more,
        }

    register("session.history", session_history)

    async def session_interrupt(params: dict) -> dict:
        runtime = _require_runtime(params)
        if (
            runtime.chat_task is not None
            and not runtime.chat_task.cancelling()
            and (runtime.active_turn is None or runtime.active_turn.running)
        ):
            runtime.chat_task.cancel()
        return {}

    register("session.interrupt", session_interrupt)

    async def session_set_settings(params: dict) -> dict:
        runtime = _require_runtime(params)
        try:
            settings_patch = SessionSettingsPatch.model_validate(params.get("settings")).model_dump(exclude_unset=True)
        except ValidationError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "Invalid session parameters") from exc
        async with SESSION_LOCAL() as db:
            conv = (
                await db.execute(
                    select(Conversation)
                    .where(Conversation.id == runtime.conversation_id, Conversation.user_id == user_id)
                    .with_for_update(),
                )
            ).scalar_one_or_none()
            if conv is None:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, "Session not found")
            settings = decode_session_settings(conv.settings_json)
            for key, value in settings_patch.items():
                if value is not None:
                    settings[key] = value
                else:
                    settings.pop(key, None)
            if settings_patch:
                conv.settings_json = json.dumps(settings, ensure_ascii=False)
                await db.commit()
            runtime.settings = settings
        return {
            "session_id": runtime.session_id,
            "settings": dict(runtime.settings),
            "info": (await _runtime_info(runtime, conv)).model_dump(),
        }

    register("session.set_settings", session_set_settings)

    async def session_compress_context(params: dict) -> dict:
        runtime = _require_runtime(params)
        _reject_read_only_session(runtime)
        async with desktop_history_lock(runtime.session_id):
            if runtime.busy:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "当前会话有正在生成的回复，请稍后再试")
            async with SESSION_LOCAL() as db:
                conv = await _require_owned_conv(db, user_id, runtime.session_id)
            result = await _do_compress_history(conv, user_id, runtime)
            if result["compressed"]:
                await publish_history_change(
                    "command.result",
                    {"command": "compress", "result": {"status": "ok", "hydrate": True, "payload": result}},
                    session_id=runtime.session_id,
                )
            return result

    register("session.compress_context", session_compress_context)

    async def session_undo_to_message(params: dict) -> dict:
        """就地截断会话并以 anchor 字段返回锚点消息的用户正文与图片附件，供客户端落回输入框作为草稿。需要 ``confirmed=true``；in-flight 拒绝；仅 ``kind='standard'`` 允许。广播 ``message.deleted`` 事件给同 user 其他窗口。"""
        session_id = _require_str(params, "session_id")
        if not bool(params.get("confirmed")):
            raise JsonRpcError(
                JSONRPC_SLASH_CONFIRM_REQUIRED,
                "session.undo_to_message requires confirmed=true",
                data={"requires_confirmation": True},
            )
        source_message_id = _require_nonneg_int(params, "source_message_id")
        # 锁与在途检查按会话主键的规范形式取键："0123" 等别名与 "123" 指向同一会话。
        async with SESSION_LOCAL() as db:
            owned = await _require_owned_conv(db, user_id, session_id)
        session_id = str(owned.id)
        runtime = runtime_sessions.get(session_id)
        async with desktop_history_lock(session_id), SESSION_LOCAL() as db:
            if runtime is not None and runtime.busy:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "当前会话有正在生成的回复，请稍后再试")
            try:
                conv = await resolve_undo_target(db, user_id, session_id, source_message_id)
                await prune_videos_in_range(db, conv.id, lo=source_message_id)
                result = await undo_conversation_to_message(db, conv, source_message_id)
                await cleanup_user_media(user_id)
            except (UndoNotAllowedError, SourceNotFoundError) as e:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(e))

        await publish_history_change(
            "message.deleted",
            {
                "session_id": session_id,
                "deleted_count": result.deleted_count,
                "anchor": result.anchor.model_dump(),
                "messages": result.messages,
            },
            session_id=session_id,
        )
        logger.info(
            "session.undo_to_message",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "source_message_id": source_message_id,
                "deleted_count": result.deleted_count,
            },
        )
        return result.model_dump(mode="json")

    register("session.undo_to_message", session_undo_to_message)

    async def command_dispatch(params: dict) -> dict:
        """Slash 命令分发：按 command 查 SLASH_COMMANDS 并执行 handler，返回 {command, result} 并同步广播 command.result 事件给同 session 各窗口。"""
        runtime = _require_runtime(params)
        raw_command = params.get("command")
        if not isinstance(raw_command, str) or not raw_command.strip():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "command must be a non-empty string")

        name = raw_command.strip().lstrip("/").lower()
        if not name:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "command name is empty")

        cmd = resolve_slash_command(name)
        if cmd is None or cmd.handler is None:
            suggestions = suggest_commands(name)
            raise JsonRpcError(
                JSONRPC_INVALID_PARAMS,
                f"unknown command: /{name}",
                data={"suggestions": suggestions},
            )

        confirmed = bool(params.get("confirmed"))
        if cmd.requires_confirmation and not confirmed:
            raise JsonRpcError(
                JSONRPC_SLASH_CONFIRM_REQUIRED,
                f"command /{name} requires explicit confirmation",
                data={"requires_confirmation": True, "command": cmd.name},
            )

        args_raw = params.get("args")
        args_list: list[str] = []
        if isinstance(args_raw, list):
            args_list = [str(a) for a in args_raw if isinstance(a, str | int | float)]
        elif isinstance(args_raw, str):
            args_list = args_raw.split()

        ctx = SlashCommandContext(
            session_id=runtime.session_id,
            user_id=user_id,
            runtime=runtime,
            args=args_list,
        )

        try:
            result = await cmd.handler(ctx)
        except JsonRpcError:
            raise
        except Exception:
            logger.exception("slash command handler failed", extra={"command": cmd.name, "user_id": user_id})
            raise JsonRpcError(JSONRPC_SLASH_GENERIC, f"command /{name} failed unexpectedly") from None

        result_payload = {"command": cmd.name, "result": asdict(result)}
        await publish_history_change("command.result", result_payload, session_id=runtime.session_id)
        return result_payload

    register("command.dispatch", command_dispatch)

    async def command_list(_params: dict) -> dict:
        """列出可用 slash 命令元数据；供客户端自动补全与确认弹窗使用。"""
        return {"commands": list_commands_for_user()}

    register("command.list", command_list)

    async def _submit_prompt(params: dict, runtime: RuntimeSession, admission: contextlib.ExitStack) -> dict:
        response_preference = params.get("response_preference")
        if response_preference is not None and response_preference not in ("text", "voice"):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "response_preference must be text or voice")

        edit_message_id = params.get("edit_message_id")
        retry_message_id = params.get("retry_message_id")
        if retry_message_id is not None:
            if not _is_nonneg_int(retry_message_id) or retry_message_id == 0:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "retry_message_id must be a positive int")
            if any(key in params for key in ("text", "batch", "attachments", "edit_message_id")):
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "reply retries reuse the original message and attachments")
        if edit_message_id is not None:
            if not _is_nonneg_int(edit_message_id) or edit_message_id == 0:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "edit_message_id must be a positive int")
            if "batch" in params or "attachments" in params:
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    "message edits only accept text; original attachments are retained",
                )

        precursor_user_message_ids: list[int] = []
        batch = params.get("batch")
        if retry_message_id is not None:
            async with SESSION_LOCAL() as db:
                try:
                    source = await get_reply_retry_message(db, user_id, runtime.session_id, retry_message_id)
                except ReplyRetryNotAllowedError as exc:
                    raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
                text = message_text(source)
                attachments = []
        elif batch is not None:
            if not isinstance(batch, list) or not batch:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "batch must be a non-empty list")
            validated_batch = []
            for item in batch:
                if not isinstance(item, dict):
                    raise JsonRpcError(JSONRPC_INVALID_PARAMS, "each item in batch must be an object")
                t = _require_str(item, "text")
                att = _validate_attachments(item, runtime.session_id)
                validated_batch.append({"text": t, "attachments": att})
            # 整批合为一个回合，附件上限按全批合计。
            if sum(len(item["attachments"] or ()) for item in validated_batch) > SETTINGS.max_attachments_per_turn:
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"too many attachments (max {SETTINGS.max_attachments_per_turn})",
                )

            last_item = validated_batch[-1]
            text = last_item["text"]
            attachments = last_item["attachments"]
            precursor_items = validated_batch[:-1]
            if precursor_items:
                async with SESSION_LOCAL() as db:
                    precursor_user_message_ids = await persist_extra_user_messages(
                        db,
                        runtime.conversation_id,
                        precursor_items,
                    )
        else:
            text = _require_str(params, "text")
            attachments = _validate_attachments(params, runtime.session_id)

        req = ChatRequest(
            session_id=runtime.session_id,
            message=ChatMessageRequest(content=text, attachments=attachments),
            response_preference=response_preference,
        )

        snapshot = ActiveTurnSnapshot(request_id=params["request_id"], origin_kind=session.origin_kind)

        emitter = JsonRpcEmitter(
            dispatcher=publisher,
            session_id=runtime.session_id,
            active_turn=snapshot,
            snapshot_lock=runtime.snapshot_lock,
        )

        persisted_message_id: int | None = retry_message_id
        edited_messages: list[dict] | None = None
        if edit_message_id is not None:
            async with SESSION_LOCAL() as db:
                try:
                    replacement = await replace_last_user_message(
                        db,
                        user_id,
                        runtime.session_id,
                        edit_message_id,
                        text,
                    )
                except EditNotAllowedError as exc:
                    raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
                await cleanup_user_media(user_id)
                persisted_message_id = replacement.id
                edited_messages = await build_session_messages(runtime.conversation_id, db)
        persisted_message_id = await persist_submission_input(
            user_id,
            runtime.conversation_id,
            params["request_id"],
            req.message,
            persisted_message_id=persisted_message_id,
            precursor_ids=precursor_user_message_ids,
        )
        if edited_messages is not None:
            await publish_history_change(
                "message.edited",
                {"session_id": runtime.session_id, "messages": edited_messages},
                session_id=runtime.session_id,
            )
        await emitter.send_json(
            {
                "type": "message.persisted",
                "role": "user",
                "message_ids": [*precursor_user_message_ids, persisted_message_id],
            },
        )
        # 输入持久化后刷新列表，不等待回复生成。
        await publisher.push_event("session.list_changed", {"session_id": runtime.session_id})
        client_context = session.session_client_context
        runtime.active_turn = snapshot
        runtime.origin_kind = session.origin_kind
        runtime.origin_id = session.origin_id
        started = False

        async def settle_turn(status: str, error: str | None) -> None:
            async with runtime.snapshot_lock:
                snapshot.running = False
                try:
                    async with asyncio.timeout(30):
                        await record_submission_state(
                            user_id,
                            params["request_id"],
                            status,
                            message_ids=snapshot.message_ids,
                            error=error,
                        )
                        await publisher.push_event("session.list_changed", {"session_id": runtime.session_id})
                        await publisher.push_event(
                            "session.state",
                            {"running": False, "request_id": params["request_id"], "status": status, "error": error},
                            session_id=runtime.session_id,
                        )
                finally:
                    if runtime.active_turn is snapshot:
                        runtime.active_turn = None

        async def run_turn() -> None:
            nonlocal started
            started = True
            status = "interrupted"
            error: str | None = None
            try:
                await record_submission_state(user_id, params["request_id"], "running")
                llm_config = await _resolve_llm_config(user_id)
                await run_chat_turn(
                    req,
                    llm_config,
                    user_id,
                    emitter,
                    session_client_context=client_context,
                    desktop_interaction=session.origin_kind == "desktop",
                    track_task=session.account.track,
                    session_settings=runtime.settings,
                    persisted_message_id=persisted_message_id,
                    final_reply_only=retry_message_id is not None,
                    authorization_check=session.authorized,
                )
                status = "failed" if emitter.failed else "completed"
                error = emitter.error
            except asyncio.CancelledError:
                error = "任务已中断，请核对已执行的操作后再继续。"
                raise
            except Exception:
                status = "failed"
                error = "本次回复未能完成，请稍后重试。"
                logger.exception("prompt.submit chat turn failed", extra={"user_id": user_id})
                await emitter.send_json({"type": "error", "message": error, "retry_message_id": persisted_message_id})
            finally:
                await settle_turn(status, error)

        runtime.chat_task = asyncio.create_task(run_turn())
        turn_activity = admission.pop_all()

        def finished(done: asyncio.Task) -> None:
            if not started and done.cancelled():
                runtime.settlement_task = asyncio.create_task(
                    settle_turn("interrupted", "任务已中断，请核对已执行的操作后再继续。"),
                )
                session.account.track(runtime.settlement_task)
                runtime.settlement_task.add_done_callback(lambda _done: turn_activity.close())
            else:
                turn_activity.close()

        runtime.chat_task.add_done_callback(finished)
        session.account.track(runtime.chat_task)
        return {"queued": True, "request_id": params["request_id"], "status": "accepted"}

    async def prompt_submit(params: dict) -> dict:
        runtime = _require_runtime(params)
        _reject_read_only_session(runtime)
        raw_id = _require_str(params, "request_id")
        try:
            request_id = str(UUID(raw_id))
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "request_id must be a UUID") from exc
        params = {**params, "session_id": runtime.session_id, "request_id": request_id}
        fingerprint = hashlib.sha256(
            json.dumps(
                {key: value for key, value in params.items() if key != "request_id"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        ).hexdigest()
        try:
            existing = await find_submission(user_id, runtime.conversation_id, request_id, fingerprint)
            if existing is not None:
                return existing.to_result()
            with contextlib.ExitStack() as admission:
                admission.enter_context(user_turn_activity(user_id, enabled=True))
                await interrupt_user_event_tasks(user_id, COMPANION_TURN_EVENT)
                async with desktop_history_lock(runtime.session_id):
                    if runtime.busy:
                        raise JsonRpcError(
                            JSONRPC_TURN_BUSY,
                            "当前会话有正在生成的回复，请稍后再试",
                            data={"reason": "turn_busy"},
                        )
                    async with SESSION_LOCAL() as db:
                        await _require_owned_conv(db, user_id, runtime.session_id)
                    receipt, created = await reserve_submission(
                        user_id,
                        runtime.conversation_id,
                        request_id,
                        fingerprint,
                        session.origin_kind,
                        session.origin_id,
                    )
                    if not created:
                        return receipt.to_result()
                    try:
                        return await _submit_prompt(params, runtime, admission)
                    except BaseException:
                        if not runtime.busy:
                            runtime.active_turn = None
                        await record_submission_state(
                            user_id,
                            request_id,
                            "failed",
                            error="消息未被受理，请修改后重新发送。",
                        )
                        raise
        except SubmissionConflictError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc

    register("prompt.submit", prompt_submit)

    async def tool_result_handler(params: dict) -> dict:
        call_id = params.get("call_id")
        result = params.get("result")
        if not isinstance(call_id, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "call_id must be a string")
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        if not resolve_future(user_id, call_id, result):
            logger.warning("Future not found or already done", extra={"user_id": user_id, "call_id": call_id})
        return {}

    register("tool.result", tool_result_handler)

    async def tools_sync(params: dict) -> dict:
        tools = params.get("tools", [])
        if not isinstance(tools, list):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools must be a list")
        if not all(isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"] for tool in tools):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "each tool must be an object with a non-empty name")
        accepted: list[dict[str, Any]] = []
        for tool in tools:
            # 与服务端工具同名的本机工具永远派发不到（服务端优先），一并下发还会让模型看到重名工具。
            if REGISTRY.get_location(user_id, tool["name"]) in ("backend", "memory"):
                logger.warning(
                    "runner tool shadows a backend tool; skipped",
                    extra={"user_id": user_id, "tool_name": tool["name"]},
                )
                continue
            accepted.append(tool)
        REGISTRY.update_runner_tools(user_id, accepted)
        return ToolsSyncResult(count=len(accepted)).model_dump()

    register("tools.sync", tools_sync)

    async def image_attach(params: dict) -> dict:
        # 路径属于用户本机，后端视为不透明引用，LLM 通过 Runner 文件工具读取。
        path = _require_str(params, "path").replace("\\", "/")
        return ImageAttachResponse(ref_text=f"@file:{path}").model_dump()

    register("image.attach", image_attach)

    async def companion_set_timezone(params: dict) -> dict:
        # Desktop 每次连接上报本地 IANA 时区：夜间批处理与互动统计按用户本地日聚合，缺这一行时整个夜间流水线会静默跳过。
        tz = params.get("timezone")
        if not isinstance(tz, str) or not tz.strip():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "timezone must be a non-empty string")
        normalized = tz.strip()
        async with SESSION_LOCAL() as db:
            if not await record_user_timezone(db, user_id, normalized):
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"unknown timezone: {normalized}")
            await db.commit()
        invalidate_user_interaction_stats(user_id)
        return {"timezone": normalized}

    register("companion.set_timezone", companion_set_timezone)

    async def companion_idle_expression(params: dict) -> dict:
        # desktop idle 监视器在阈值+冷却后调用；LLM 决定是否播一个动作，播放走统一 play_requested。
        if await get_disturbance_tier(user_id) != "autonomous":
            return {"expressed": False, "action_id": None, "reason": "autonomous tier required"}
        now = time.monotonic()
        if _user_throttled(
            _last_idle_expression_ts,
            user_id,
            SETTINGS.companion_idle_expression_min_interval_seconds,
            now,
        ):
            logger.debug(
                "idle_expression: throttled",
                extra={"user_id": user_id, "since_sec": round(now - _last_idle_expression_ts.get(user_id, 0.0), 3)},
            )
            return {"expressed": False, "action_id": None, "reason": "throttled"}
        _last_idle_expression_ts[user_id] = now

        idle_seconds = coerce_non_negative_float(params.get("idle_seconds"))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        presentation = get_presentation_snapshot(user_id)
        if presentation is not None and presentation.mode == "desktop":
            return await choose_idle_desktop_action(user_id, idle_seconds, local_hour)
        llm_config = await _resolve_llm_config(user_id)
        result = await check_idle_expression(user_id, idle_seconds, local_hour, llm_config)
        if presentation != get_presentation_snapshot(user_id):
            return {"expressed": False, "action_id": None, "reason": "presentation changed"}
        if result.expressed and result.action_id is not None:
            async with SESSION_LOCAL() as db:
                play = await request_playback(
                    db,
                    user_id,
                    ActionPlayRequest(action_id=result.action_id, reason="idle expression"),
                    source="autonomous",
                )
                if presentation != get_presentation_snapshot(user_id):
                    return {"expressed": False, "action_id": None, "reason": "presentation changed"}
                await db.commit()
            return {
                "expressed": play.outcome == "queued",
                "action_id": result.action_id,
                "reason": play.message or result.reason,
            }
        return {
            "expressed": False,
            "action_id": None,
            "reason": result.reason,
        }

    register("companion.idle_expression", companion_idle_expression)

    async def companion_signal(params: dict) -> dict[str, bool]:
        try:
            signal = CompanionSignal.model_validate(params)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        became_available = observe_companion_presence(
            user_id,
            signal.available,
            presentation_mode=signal.presentation_mode,
        )
        if not signal.available:
            await interrupt_user_event_tasks(user_id, COMPANION_TURN_EVENT)
            return {"queued": False}
        queued = await queue_companion_intent(user_id, "desktop_available" if became_available else signal.event)
        if became_available and signal.event == "context_changed":
            queued = await queue_companion_intent(user_id, signal.event) or queued
        return {"queued": queued}

    register("companion.signal", companion_signal)

    async def companion_record_interaction_stats(params: dict) -> dict:
        # chat_turn 每事件统计供每日 Memory 汇总（无 LLM 开销），desktop 侧合并到 STATS_THRESHOLD 后切分钟级节流；hour 是用户本地小时，与本地日期键同口径。
        kind = params.get("kind")
        hour = params.get("hour")
        if not isinstance(hour, int) or not 0 <= hour <= 23:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "hour must be int in [0, 23]")
        if kind != "chat_turn":
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"kind must be chat_turn, got {kind!r}")
        return await record_interaction(user_id, kind, hour)

    register("companion.record_interaction_stats", companion_record_interaction_stats)

    async def companion_should_act(params: dict) -> dict:
        kind = params.get("kind", "periodic_provision")
        if kind != "periodic_provision":
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"kind must be periodic_provision, got {kind!r}")

        # 空间自主决策只服务自主档；客户端另以桌面精灵可见性拦截。
        if await get_disturbance_tier(user_id) != "autonomous":
            return {"should_act": False, "action": "stay", "reason": "autonomous tier required"}

        presentation = get_presentation_snapshot(user_id)
        if presentation is not None and presentation.mode == "desktop":
            return {"should_act": False, "action": "stay", "reason": "window presentation required"}

        now = time.monotonic()
        if _user_throttled(_last_should_act_ts, user_id, SHOULD_ACT_ANTIDUP_SECONDS, now):
            return {"should_act": False, "action": "stay", "reason": "throttled"}
        _last_should_act_ts[user_id] = now

        idle_seconds = coerce_non_negative_float(params.get("idle_seconds"))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        focused_category = params.get("focused_category")
        if focused_category is not None and not isinstance(focused_category, str):
            focused_category = None
        fullscreen = bool(params.get("fullscreen"))
        screen_locked = bool(params.get("screen_locked"))
        seconds_since_last_action = coerce_non_negative_float(params.get("seconds_since_last_action"))

        llm_config = await _resolve_llm_config(user_id)
        presentation = get_presentation_snapshot(user_id)
        res = await should_act(
            user_id=user_id,
            idle_seconds=idle_seconds,
            local_hour=local_hour,
            focused_category=focused_category,
            fullscreen=fullscreen,
            screen_locked=screen_locked,
            seconds_since_last_action=seconds_since_last_action,
            llm_config=llm_config,
        )
        if presentation != get_presentation_snapshot(user_id):
            return {"should_act": False, "action": "stay", "reason": "presentation changed"}
        # 走过去搭话（DESIGN「位置、移动与缩放」）：开场白经 companion.message 独立投递、客户端边走边说，RPC 响应只承载走位动作；should_act 已把 approach 的 params 收敛为非空 text。
        if (
            res.action == "approach"
            and res.params is not None
            and not await emit_companion_message(user_id, res.params["text"], presentation=presentation)
        ):
            return {"should_act": False, "action": "stay", "reason": "presentation changed"}
        return res.model_dump()

    register("companion.should_act", companion_should_act)

    async def _memory_scope(params: dict, db: AsyncSession) -> MemoryScope:
        try:
            if "session_id" in params:
                if "system_preset_id" in params or not isinstance(params["session_id"], str):
                    raise ValueError("Provide a session or a preset, not both")
                return await resolve_memory_scope(db, user_id, params["session_id"])
            preset = params.get("system_preset_id")
            if not isinstance(preset, str):
                raise ValueError("session_id or system_preset_id is required")
            scope = MemoryScope(user_id, preset)
            validate_memory_scope(scope)
            return scope
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc

    async def memory_list(params: dict) -> dict:
        kind = params.get("kind")
        tag = params.get("tag")
        q = params.get("q")
        limit = params.get("limit")
        try:
            async with SESSION_LOCAL() as db:
                scope = await _memory_scope(params, db)
                rows = await list_memories(
                    db,
                    scope,
                    kind=kind if isinstance(kind, str) else None,
                    status=params.get("status", "active"),
                    tag=tag if isinstance(tag, str) else None,
                    q=q if isinstance(q, str) else None,
                    limit=int(limit) if isinstance(limit, int) else 100,
                )
                counts = await memory_counts(db, scope)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))
        return {
            "memories": [row.model_dump() for row in rows],
            "counts": counts,
            "system_preset_id": scope.system_preset_id,
            "session_id": params.get("session_id"),
        }

    async def memory_update(params: dict) -> dict:
        memory_id = params.get("memory_id")
        content = params.get("content")
        expected_version = params.get("expected_version")
        if (
            not isinstance(memory_id, int)
            or not isinstance(content, str)
            or type(expected_version) is not int
            or expected_version < 1
        ):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "memory_id (int) and content (str) required")
        try:
            async with SESSION_LOCAL() as db:
                scope = await _memory_scope(params, db)
            row = await update_memory(scope, memory_id, content=content, expected_version=expected_version)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))
        if row is None:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"memory {memory_id} not found")
        return row.model_dump()

    async def memory_delete(params: dict) -> dict:
        memory_id = params.get("memory_id")
        if not isinstance(memory_id, int):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "memory_id (int) required")
        async with SESSION_LOCAL() as db:
            scope = await _memory_scope(params, db)
            ok = await delete_memory(db, scope, memory_id)
        if not ok:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"memory {memory_id} not found")
        return {"deleted": ok}

    register("memory.list", memory_list)
    register("memory.update", memory_update)
    register("memory.delete", memory_delete)

    async def onboarding_get_state(_params: dict) -> dict:
        async with SESSION_LOCAL() as db:
            return (await get_onboarding_state(db, user_id)).model_dump()

    async def onboarding_submit(params: dict) -> dict:
        # 每字段增量落库，崩溃最多丢当前一题。
        field = params.get("field")
        if not isinstance(field, str) or not field:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "field must be a non-empty string")
        value = params.get("value")
        if value is not None and not isinstance(value, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "value must be a string or null")
        async with SESSION_LOCAL() as db:
            try:
                return (
                    await submit_onboarding_field(
                        db,
                        user_id,
                        field,
                        value,
                        expected_version=params.get("expected_version"),
                    )
                ).model_dump()
            except PersonaValidationError as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))

    register("onboarding.get_state", onboarding_get_state)
    register("onboarding.submit", onboarding_submit)

    async def avatar_regenerate(params: dict) -> dict:
        # 10-60s 同步生图以后台 task 跑，立即返回 queued: true 不阻塞 WS 接收循环；结果通过 avatar.regenerated 事件回。
        feedback = _optional_str(params, "feedback")
        try:
            feedback = AvatarGenerateRequest(feedback=feedback).feedback
        except ValidationError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "头像修改描述不能超过 500 字") from exc
        mode = params.get("mode")
        if mode not in ("edit", "regenerate"):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "mode must be 'edit' or 'regenerate'")
        async with SESSION_LOCAL() as db:
            persona = await get_or_create_persona(db, user_id)
            if not persona.is_complete:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "finish onboarding before regenerating avatar")
            # DESIGN「身份锁定与角色卡」 形象锁定：确认后重生路径关闭——即时拒绝而非后台任务失败
            await raise_if_image_sealed(db, user_id, persona)
        job_id = f"avatar_regen_{user_id}_{secrets.token_urlsafe(6)}"
        lock = get_avatar_job_lock(user_id)
        if lock.locked():
            return {"queued": False, "reason": "already_running", "error": "头像正在生成，请等待当前任务完成"}

        async def _run() -> None:
            try:
                asset = await regenerate_avatar(user_id=user_id, feedback=feedback, mode=mode)
                payload = {"job_id": job_id, "asset_url": avatar_response(asset).asset_url, "id": asset.id}
            except AvatarGenerationError as exc:
                logger.warning("avatar regenerate failed", extra={"user_id": user_id, "error": exc.internal})
                payload = {"job_id": job_id, "error": str(exc)}
            except Exception:
                logger.exception("avatar regenerate unexpected failure", extra={"user_id": user_id})
                payload = {"job_id": job_id, "error": "伙伴形象生成失败，请稍后重试"}
            try:
                await publisher.push_event("avatar.regenerated", payload, session_id=None)
            except Exception:
                logger.debug("avatar.regenerated event push failed", extra={"user_id": user_id}, exc_info=True)

        session.track(asyncio.create_task(_run()))
        return {"queued": True, "job_id": job_id}

    register("avatar.regenerate", avatar_regenerate)

    async def tts_list_voices(params: dict) -> dict:
        # 可选 language 过滤——未知值直接返回完整目录，避免将来新增 tag 时 400。
        language = normalize_voice_language(_optional_str(params, "language"))
        async with SESSION_LOCAL() as db:
            return (await list_tts_voices(db, user_id, language=language)).model_dump()

    async def tts_match_voice(params: dict) -> dict:
        # onboarding 不为已有目录覆盖的窄标签任务付 LLM 延迟，直接映射到已配置供应商目录中的 voice id。
        preference = _require_str(params, "preference")
        language = normalize_voice_language(_optional_str(params, "language"))
        async with SESSION_LOCAL() as db:
            return (await match_user_voice(db, user_id, preference, language=language)).model_dump()

    async def tts_design_voice(params: dict) -> dict:
        prompt = params.get("prompt")
        if not isinstance(prompt, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "prompt must be a non-empty string")
        prompt = prompt.strip()
        if not prompt or len(prompt) > MAX_VOICE_DESIGN_PROMPT_CHARS:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"prompt must be 1..{MAX_VOICE_DESIGN_PROMPT_CHARS} chars")
        preview_text = params.get("preview_text")
        if not isinstance(preview_text, str):
            preview_text = ""
        if len(preview_text) > _VOICE_PREVIEW_TEXT_MAX_CHARS:
            raise JsonRpcError(
                JSONRPC_INVALID_PARAMS,
                f"preview_text must be at most {_VOICE_PREVIEW_TEXT_MAX_CHARS} chars",
            )
        try:
            result = await design_voice(user_id, prompt, preview_text=preview_text)
        except (ValueError, MissingLlmConfigError) as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return {
            "provider": result.provider,
            "voice_id": result.voice_id,
            "trial_audio_base64": base64.b64encode(result.trial_audio).decode("ascii"),
            "trial_audio_mime": result.trial_audio_mime,
        }

    register("tts.list_voices", tts_list_voices)
    register("tts.match_voice", tts_match_voice)
    register("tts.design_voice", tts_design_voice)
