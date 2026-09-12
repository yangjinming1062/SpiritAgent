import asyncio
import base64
import contextlib
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from components import (
    ATTACHMENT_DATA_URL_MAX_CHARS,
    ATTACHMENT_TYPE_IMAGE,
    ATTACHMENT_TYPE_VIDEO,
    CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT,
    DEFAULT_LANGUAGE,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_SLASH_BUSY,
    JSONRPC_SLASH_CONFIRM_REQUIRED,
    JSONRPC_SLASH_GENERIC,
    MAX_ATTACHMENTS_PER_TURN,
    MAX_RECALL_CONTENT_CHARS,
    MAX_VOICE_DESIGN_PROMPT_CHARS,
    REQUEST_ID_HEADER,
    SESSION_HISTORY_PRE_BUFFER,
    SESSION_HISTORY_TRUNCATE_THRESHOLD,
    SESSION_LOCAL,
    SESSION_TO_GLOBAL_KEY_ALIASES,
    SETTINGS,
    adopt_inbound,
    coerce_hour_0_23,
    coerce_non_negative_float,
    coerce_non_negative_int,
    get_logger,
    path_attach_ref,
    safe_json_loads,
)
from components.user_maintenance_runtime import is_user_in_maintenance
from fastapi import WebSocket, WebSocketDisconnect
from modules.auth import ChatRequestClientContext
from modules.conversation import Conversation, Message
from modules.system import ChatMessageRequest, ChatRequest, PromptPresetListResponse, PromptPresetSummary
from modules.ws import CRON_TURN_EVENT
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.adapters.desktop.auth import authenticate_ws_token, is_ws_login_active
from services.application.chat import (
    SlashCommandContext,
    SlashCommandResult,
    build_turn_inputs,
    list_commands_for_user,
    load_user_settings,
    merge_session_settings,
    parse_temperature,
    persist_extra_user_messages,
    register_slash_command,
    resolve_inference_settings,
    resolve_slash_command,
    run_chat_turn,
    suggest_commands,
)
from services.application.chat.context_compressor import compress_history_if_needed
from services.application.generation import (
    AVATAR_JOB_LOCKS,
    MODEL_JOB_LOCKS,
    AvatarGenerationError,
    ModelGenerationError,
    get_avatar_job_lock,
    raise_if_image_sealed,
    regenerate_avatar,
    request_model_download_retry,
)
from services.contracts.memory import EmbeddingItem, MemoryScope, MemorySource
from services.domains.companion import (
    REGION_NAMES_ZH,
    PersonaValidationError,
    check_affect,
    design_voice,
    emit_companion_message,
    get_onboarding_state,
    get_or_create_persona,
    interact,
    list_tts_voices,
    match_user_voice,
    normalize_voice_language,
    record_interaction,
    should_act,
    submit_onboarding_field,
)
from services.domains.companion.disturbance import get_disturbance_tier
from services.domains.companion.interaction_stats import invalidate_user_interaction_stats
from services.domains.conversation import (
    IM_KIND,
    SYSTEM_PRESET_CATALOG,
    ForkNotAllowedError,
    SourceNotFoundError,
    UndoNotAllowedError,
    build_session_messages,
    conversation_memory_scope,
    fork_conversation_from_message,
    get_or_create_special_conversation,
    get_special_conversation,
    note_user_contact,
    reset_user_outreach,
    resolve_memory_scope,
    resolve_undo_target,
    undo_conversation_to_message,
    validate_memory_scope,
)
from services.domains.media import prune_videos_in_range
from services.domains.memory import (
    backfill_memory_embeddings,
    create_memory,
    delete_memory,
    list_memories,
    memory_counts,
    normalize_recall_context,
    read_user_profile,
    record_user_timezone,
    update_memory,
)
from services.infrastructure.desktop.buffer import (
    DEFAULT_REPLAY_BUFFER_CAPACITY,
    DEFAULT_REPLAY_BUFFER_TTL_SECONDS,
    ReplayBuffer,
)
from services.infrastructure.desktop.connection import MANAGER
from services.infrastructure.desktop.ipc import discard_user, resolve_future
from services.infrastructure.desktop.jsonrpc import JsonRpcDispatcher, JsonRpcError
from services.infrastructure.event_store import (
    cancel_user_event_tasks,
    interrupt_user_event_tasks,
)
from services.infrastructure.llm import MissingLlmConfigError, resolve_user_llm_config, scale_temperature
from services.infrastructure.tool_runtime import REGISTRY

from .emitter import JsonRpcEmitter
from .runtime import (
    RuntimeSession,
    SessionCreateResult,
    SessionResumeResult,
    SessionSettingsPatch,
    ToolsSyncResult,
    new_runtime_session,
    runtime_info_snapshot,
)

logger = get_logger(__name__)

DISCONNECT_GRACE_SECONDS = 30.0


@dataclass
class UserGatewaySession:
    user_id: int
    login_record_id: int
    dispatcher: JsonRpcDispatcher
    replay_buffer: ReplayBuffer
    runtime_sessions: dict[str, RuntimeSession] = field(default_factory=dict)
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    llm_config: dict = field(default_factory=dict)
    user_settings: dict = field(default_factory=dict)
    session_client_context: ChatRequestClientContext | None = None
    grace_timer_task: asyncio.Task | None = None
    websocket: WebSocket | None = None


_USER_SESSIONS: dict[int, UserGatewaySession] = {}
_GRACE_TIMER_TASKS: set[asyncio.Task] = set()


def discard_user_session(user_id: int) -> list[asyncio.Task]:
    """注销某用户的 session 桶并触发 per-task 取消；返回被取消的 task 列表，调用方需在 DB 行删除前 ``asyncio.gather`` 它们。"""
    sess = _USER_SESSIONS.pop(user_id, None)
    if sess is None:
        return []
    pending: list[asyncio.Task] = []
    current_task = asyncio.current_task()
    if sess.grace_timer_task and not sess.grace_timer_task.done() and sess.grace_timer_task is not current_task:
        sess.grace_timer_task.cancel()
        pending.append(sess.grace_timer_task)
    for t in list(sess.background_tasks):
        if not t.done() and t is not current_task:
            t.cancel()
            pending.append(t)
    for runtime in list(sess.runtime_sessions.values()):
        if runtime.chat_task and not runtime.chat_task.done() and runtime.chat_task is not current_task:
            runtime.chat_task.cancel()
            pending.append(runtime.chat_task)
    if (
        sess.dispatcher._writer_task
        and not sess.dispatcher._writer_task.done()
        and sess.dispatcher._writer_task is not current_task
    ):
        sess.dispatcher._writer_task.cancel()
        pending.append(sess.dispatcher._writer_task)
    sess.runtime_sessions.clear()
    return pending


async def drain() -> None:
    """取消 UserGatewaySession 中所有 per-user background task。"""
    pending: list[asyncio.Task] = []
    for sess in _USER_SESSIONS.values():
        pending.extend(sess.background_tasks)
    pending.extend(_GRACE_TIMER_TASKS)
    if not pending:
        return
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _noop_send(data: dict[str, Any]) -> None:
    pass


async def _noop_send_strict(data: dict[str, Any]) -> bool:
    return False


# 进程级节流：buggy renderer 可能狂打 check_affect 烧 LLM 配额。
CHECK_AFFECT_MIN_INTERVAL_SECONDS = 2.0
_last_check_affect_ts: dict[int, float] = {}

INTERACT_MIN_INTERVAL_SECONDS = 1.5
USER_TRIGGERED_LLM_COOLDOWN_SECONDS = 5 * 60
# 失败短冷却：5 分钟成本窗口只在成功时全额消耗的话，供应商持续故障时连戳会把成本闸门
# 打成筛子；60s 让故障模式也有封顶，又不把瞬时失败的用户锁满 5 分钟
INTERACT_FAILURE_COOLDOWN_SECONDS = 60
_last_interact_ts: dict[int, float] = {}
# per-(user, kind) 存冷却到期时刻（monotonic），成功与失败分别写 5min / 60s
_llm_cooldown_until: dict[int, dict[str, float]] = {}
# per-(user, kind) 的 in-flight 守卫：慢的 LLM 反应还没回时，避免再触发第二次并发反应。
_inflight_interact: set[tuple[int, str]] = set()

# per-user 的 prompt.submit（renderer 发起的 chat turn）in-flight 守卫；companion.interact 读它，保证用户正在输入时戳一戳反应不会落下——否则两条路径会向同一个主会话交叉写入行，下一次 LLM 上下文里时间线就乱了。
_inflight_prompt: set[int] = set()

SHOULD_ACT_ANTIDUP_SECONDS = 2.0
_last_should_act_ts: dict[int, float] = {}


# avatar.* RPC 创建的 task，关停时由 drain 收尾。
_avatar_regen_tasks: set[asyncio.Task] = set()

# avatar regen 的 advisory lock：防止并发 regen 互相覆盖；xact 版本在 commit/rollback 时自动释放，无需显式解锁；与 user_id 组合后每个用户独占一个槽。
_AVATAR_REGEN_ADVISORY_NAMESPACE = 0x4156_4156

# Slash 命令 per-conversation 锁：防止 /清理 + /压缩 双击 / 多窗口并发触发导致重复 marker。
# 串行化副作用：DB 事务已是原子的，但 marker 是 INSERT，可能产生重复 status_cleared 行。
_conversation_locks: dict[str, asyncio.Lock] = {}


def _clear_user_gateway_state(user_id: int) -> None:
    REGISTRY.clear_runner_tools(user_id)
    discard_user(user_id)
    _inflight_prompt.discard(user_id)
    _inflight_interact.difference_update({item for item in _inflight_interact if item[0] == user_id})
    _last_interact_ts.pop(user_id, None)
    _llm_cooldown_until.pop(user_id, None)
    _last_check_affect_ts.pop(user_id, None)
    _last_should_act_ts.pop(user_id, None)
    AVATAR_JOB_LOCKS.pop(user_id, None)
    MODEL_JOB_LOCKS.pop(user_id, None)


async def _terminate_user_gateway_locked(user_id: int, login_record_id: int | None = None) -> bool:
    sess = _USER_SESSIONS.get(user_id)
    active_login_id = sess.login_record_id if sess is not None else MANAGER.get_login_record_id(user_id)
    if login_record_id is not None and active_login_id != login_record_id:
        return False

    websocket = MANAGER.active_connections.get(user_id)
    if websocket is not None:
        with contextlib.suppress(Exception):
            await websocket.close(code=1008)
        MANAGER.disconnect(websocket, user_id)

    pending = discard_user_session(user_id)
    await interrupt_user_event_tasks(user_id, CRON_TURN_EVENT)
    await MANAGER.aunregister_dispatcher(user_id)
    _clear_user_gateway_state(user_id)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return sess is not None or websocket is not None


async def terminate_user_gateway(user_id: int, *, login_record_id: int | None = None) -> bool:
    lock = _HANDSHAKE_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        return await _terminate_user_gateway_locked(user_id, login_record_id)


def _user_throttled(state: dict[int, float], user_id: int, min_interval: float, now: float) -> bool:
    """若用户仍在窗口内返回 True 且不更新时间戳。"""
    return now - state.get(user_id, 0.0) < min_interval


class WSEmitter:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def send_json(self, data: dict[str, Any]) -> None:
        await self.send_json_strict(data)

    async def send_json_strict(self, data: dict[str, Any]) -> bool:
        try:
            await self.websocket.send_json(data)
            return True
        except WebSocketDisconnect:
            return False
        except RuntimeError as e:
            if "close message" not in str(e):
                logger.debug("WSEmitter.send_json_strict RuntimeError", extra={"error": str(e)})
            return False
        except Exception as e:
            logger.debug("WSEmitter.send_json_strict unexpected", extra={"error": str(e)}, exc_info=True)
            return False


_HANDSHAKE_LOCKS: dict[int, asyncio.Lock] = {}


async def handle_chat_websocket(websocket: WebSocket, token: str) -> None:
    # BaseHTTPMiddleware 跳过 WS upgrade——在 authenticate 前从 upgrade 的 X-Request-ID 重建 request_id，auth 失败行的日志才不会丢关联。
    adopt_inbound(websocket.headers.get(REQUEST_ID_HEADER))

    # accept 前先 authenticate；被拒握手在传输层快速失败为 1008，不占 ConnectionManager 槽位。
    user, payload = await authenticate_ws_token(token)
    if user is None:
        await websocket.close(code=1008)
        return

    user_id = user.id
    login_record_id = int(payload["login_id"])
    lock = _HANDSHAKE_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        try:
            if is_user_in_maintenance(user_id) or not await is_ws_login_active(user_id, login_record_id):
                await websocket.close(code=1008)
                return

            existing_session = _USER_SESSIONS.get(user_id)
            if existing_session is not None and existing_session.login_record_id != login_record_id:
                await _terminate_user_gateway_locked(user_id)

            await MANAGER.connect(websocket, user_id, login_record_id)

            # config 和 settings 只在连接时读一次；每次工具执行都开新的 SESSION_LOCAL()，让并发工具调用不共享 SQLAlchemy 状态。
            async with SESSION_LOCAL() as boot_db:
                llm_config = await resolve_user_llm_config(boot_db, user_id)
                user_settings = await load_user_settings(boot_db, user_id)
                await get_or_create_special_conversation(boot_db, user_id, "companion")

            session_client_context: ChatRequestClientContext | None = None
            if payload and "ctx" in payload:
                try:
                    session_client_context = ChatRequestClientContext(**payload["ctx"])
                except Exception:
                    logger.debug(
                        "client context parse failed; continuing without context",
                        extra={"user_id": user_id, "ctx_keys": list((payload.get("ctx") or {}).keys())},
                    )

            ws_emitter = WSEmitter(websocket)

            existing_session = _USER_SESSIONS.get(user_id)
            if existing_session is not None:
                if existing_session.grace_timer_task and not existing_session.grace_timer_task.done():
                    existing_session.grace_timer_task.cancel()
                    existing_session.grace_timer_task = None
                existing_session.websocket = websocket
                existing_session.llm_config = llm_config
                existing_session.user_settings = user_settings
                existing_session.session_client_context = session_client_context
                existing_session.dispatcher.set_sender(ws_emitter.send_json, send_strict=ws_emitter.send_json_strict)
                existing_session.dispatcher.enable_hold()
                user_session = existing_session
                dispatcher = user_session.dispatcher
                runtime_sessions = user_session.runtime_sessions
                MANAGER.register_dispatcher(user_id, dispatcher)
                MANAGER.register_runtime_sessions(user_id, runtime_sessions)
                logger.info("Resumed active user gateway session across reconnect", extra={"user_id": user_id})
            else:
                replay_buffer = ReplayBuffer(
                    capacity=DEFAULT_REPLAY_BUFFER_CAPACITY,
                    ttl_seconds=DEFAULT_REPLAY_BUFFER_TTL_SECONDS,
                )
                dispatcher = JsonRpcDispatcher(
                    ws_emitter.send_json,
                    replay_buffer=replay_buffer,
                    send_strict=ws_emitter.send_json_strict,
                )
                dispatcher.enable_hold()
                runtime_sessions: dict[str, RuntimeSession] = {}
                background_tasks: set[asyncio.Task] = set()
                user_session = UserGatewaySession(
                    user_id=user_id,
                    login_record_id=login_record_id,
                    dispatcher=dispatcher,
                    replay_buffer=replay_buffer,
                    runtime_sessions=runtime_sessions,
                    background_tasks=background_tasks,
                    llm_config=llm_config,
                    user_settings=user_settings,
                    session_client_context=session_client_context,
                    websocket=websocket,
                )
                _USER_SESSIONS[user_id] = user_session
                MANAGER.register_dispatcher(user_id, dispatcher)
                MANAGER.register_runtime_sessions(user_id, runtime_sessions)

                _register_session_handlers(
                    dispatcher,
                    runtime_sessions,
                    llm_config,
                    user_id,
                    replay_buffer=replay_buffer,
                    user_session=user_session,
                )
        except Exception:
            logger.exception("WebSocket boot initialization failed", extra={"user_id": user_id})
            MANAGER.disconnect(websocket, user_id)
            try:
                await websocket.close(code=1011)
            except Exception:
                logger.warning(
                    "failed to close websocket after boot init failure",
                    extra={"user_id": user_id},
                    exc_info=True,
                )
            return

    try:
        while True:
            data = await websocket.receive_text()
            async with lock:
                if is_user_in_maintenance(user_id) or not await is_ws_login_active(user_id, login_record_id):
                    await _terminate_user_gateway_locked(user_id, login_record_id)
                    return
                dispatch_task = asyncio.create_task(user_session.dispatcher.handle_raw(data))
                user_session.background_tasks.add(dispatch_task)
            try:
                await dispatch_task
            except asyncio.CancelledError:
                if _USER_SESSIONS.get(user_id) is not user_session:
                    return
                raise
            except Exception:
                logger.exception("jsonrpc dispatch failed", extra={"user_id": user_id})
            finally:
                user_session.background_tasks.discard(dispatch_task)
    except WebSocketDisconnect:
        pass
    finally:
        is_active = MANAGER.active_connections.get(user_id) is websocket
        MANAGER.disconnect(websocket, user_id)
        if is_active:
            sess = _USER_SESSIONS.get(user_id)
            if sess is not None and sess.websocket is websocket:
                sess.websocket = None
                sess.dispatcher.set_sender(_noop_send, send_strict=_noop_send_strict)

                async def _grace_cleanup(uid: int) -> None:
                    try:
                        await asyncio.sleep(DISCONNECT_GRACE_SECONDS)
                        if not MANAGER.is_connected(uid):
                            logger.info(
                                "Grace period expired for disconnected user, performing full cleanup",
                                extra={"user_id": uid},
                            )
                            try:
                                pending = discard_user_session(uid)
                                if pending:
                                    await asyncio.gather(*pending, return_exceptions=True)
                            finally:
                                cancel_user_event_tasks(uid, CRON_TURN_EVENT)
                                await MANAGER.aunregister_dispatcher(uid)
                                _clear_user_gateway_state(uid)
                    except asyncio.CancelledError:
                        pass

                task = asyncio.create_task(_grace_cleanup(user_id))
                _GRACE_TIMER_TASKS.add(task)
                task.add_done_callback(_GRACE_TIMER_TASKS.discard)
                sess.grace_timer_task = task


async def _find_owned_conv(db: AsyncSession, user_id: int, session_id: str) -> Conversation | None:
    """把 renderer 给的 session_id（DB 主键）解析为 Conversation；id 不是整数、不存在或不属于 user_id 时返回 None，调用方抛错。"""
    return await Conversation.by_session_id(db, session_id, user_id=user_id)


def _require_str(params: dict[str, Any], key: str) -> str:
    """取必填字符串参数，否则抛 JSONRPC_INVALID_PARAMS。"""
    v = params.get(key)
    if not isinstance(v, str):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a string")
    return v


def _is_nonneg_int(v: Any) -> bool:
    """type(v) is int 拒绝 bool（Python 把 bool 当 int 子类）。"""
    return type(v) is int and v >= 0


def _is_session_video_url(file_url: str, session_id: str) -> bool:
    """视频附件只认本会话的后端上传 URL：相对路径（本地模式请求时内联）或 ``public_base_url`` 前缀的
    绝对形态（公网模式供应商自拉）。任意第三方绝对 URL 会让供应商替我们发任意请求，必须绑死前缀。"""
    if len(file_url) > 2048:
        return False
    if file_url.startswith(("http://", "https://")):
        base = SETTINGS.public_base_url.strip().rstrip("/")
        if not base or not file_url.startswith(f"{base}/"):
            return False
    marker = f"/api/media/videos/{session_id}/"
    pos = file_url.find(marker)
    if pos == -1:
        return False
    file_id = file_url[pos + len(marker) :]
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{10,64}\.(mp4|mov)", file_id))


def _validate_attachments(params: dict[str, Any], session_id: str) -> list[dict[str, Any]] | None:
    """校验并规范化 attachments 负载：返回清洗后的列表（每项重塑为 {type, file_url}），调用方未传时返回 None。

    image 的 file_url 接受 HTTP(S) URL 与桌面端本地图片直发的 ``data:image/*;base64,`` data URL；
    video 只接受本会话的后端上传 URL——base64 视频远超 WS 单帧上限，客户端须先经 ``POST /api/media/videos`` 换取 URL。
    """
    raw = params.get("attachments")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "attachments must be a list")
    if len(raw) > MAX_ATTACHMENTS_PER_TURN:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"too many attachments (max {MAX_ATTACHMENTS_PER_TURN})")
    cleaned: list[dict[str, Any]] = []
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
            if not _is_session_video_url(file_url, session_id):
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"attachments[{idx}].file_url must be a video URL uploaded to this session (/api/media/videos/{session_id}/...)",
                )
            cleaned.append({"type": att_type, "file_url": file_url})
            continue

        # HTTP/HTTPS URL（长度与 URL 语义一致，维持紧上限）
        if file_url.startswith("http"):
            if len(file_url) > 2048:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}].file_url too long")
            cleaned.append({"type": att_type, "file_url": file_url})
            continue

        # 桌面端本地图片 data URL：字节在负载内，不落盘；字符上限覆盖 base64 膨胀。
        if file_url.startswith("data:image/"):
            if len(file_url) > ATTACHMENT_DATA_URL_MAX_CHARS:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}].file_url too long")
            cleaned.append({"type": att_type, "file_url": file_url})
            continue

        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}] must have file_url")
    return cleaned


def _get_runtime(runtime_sessions: dict[str, RuntimeSession], params: dict[str, Any]) -> RuntimeSession:
    """按 session_id 参数查找 runtime，找不到抛 JSONRPC_METHOD_NOT_FOUND。"""
    session_id = _require_str(params, "session_id")
    runtime = runtime_sessions.get(session_id)
    if runtime is None:
        raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {session_id!r}")
    return runtime


async def _record_main_conversation(user_id: int, role: str, content: str, subtype: str) -> None:
    """向主会话追加一条 status 行：best-effort，丢失历史行不能让用户等的 RPC 失败。"""
    try:
        async with SESSION_LOCAL() as db:
            if main_conv := await get_special_conversation(db, user_id, "companion"):
                db.add(Message(conversation_id=main_conv.id, role=role, content=content, subtype=subtype))
                await db.commit()
    except Exception:
        logger.exception(
            "failed to persist main-conversation status row",
            extra={"user_id": user_id, "subtype": subtype},
        )


# 新消息类型注册常量（与 status_* 平级；不读 status_pill 路径，要走专门 subtype 渲染分支）。
MESSAGE_SUBTYPE_STATUS_CLEARED: str = "status_cleared"


async def _do_compress_history(
    db: AsyncSession,
    conv: Conversation,
    user_id: int,
    runtime: RuntimeSession,
) -> dict[str, Any]:
    """session.compress_context 与 /压缩 命令的共用实现。

    调用前必须已校验 in-flight 守卫（chat_task.done）。返回 dict 形态与 session.compress_context 一致：
    compressed=False 时不含 messages / summary；True 时含 delivered messages 给前端 hydrate。
    """
    user_settings = await load_user_settings(db, user_id)
    effective_settings = merge_session_settings(user_settings, runtime.settings, conv=conv)
    req = ChatRequest(session_id=str(conv.id), message=ChatMessageRequest(role="user", content=""))
    inputs = await build_turn_inputs(
        db,
        conv,
        user_id,
        req,
        runtime.session_client_context,
        effective_settings,
        conversation_memory_scope(conv, user_id),
    )

    compression_u = parse_temperature(
        effective_settings.get("chat.compression_temperature"),
        CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT,
    )
    compressed_context, compress_info = await compress_history_if_needed(
        inputs.context,
        client=inputs.client,
        model=inputs.model_name,
        context_length=inputs.ctx_length,
        enabled=True,
        threshold_ratio=0.0,
        temperature=scale_temperature(inputs.provider_name, compression_u),
        language=effective_settings.get("language", DEFAULT_LANGUAGE),
        current_tokens=inputs.estimated_tokens,
        force=True,
    )

    if compress_info is None:
        return {
            "session_id": runtime.session_id,
            "compressed": False,
            "reason": "历史消息较少，无需压缩（至少需要保留最近对话）",
            "usage": {
                "total_tokens": inputs.estimated_tokens,
                "context_window": inputs.ctx_length,
            },
        }

    checkpoint = Message(
        conversation_id=conv.id,
        role="system",
        content=f"[🗜️ 对话压缩 — {compress_info['replaced_count']} 条早期消息已压缩]\n{compress_info['summary']}",
        subtype="compress_summary",
        prompt_tokens=compress_info.get("prompt_tokens", 0),
        completion_tokens=compress_info.get("completion_tokens", 0),
    )
    db.add(checkpoint)
    await db.commit()
    await prune_videos_in_range(db, conv.id, hi=checkpoint.id)
    await db.commit()

    new_inputs = await build_turn_inputs(
        db,
        conv,
        user_id,
        req,
        runtime.session_client_context,
        effective_settings,
        conversation_memory_scope(conv, user_id),
    )
    delivered = await build_session_messages(conv.id, db)

    return {
        "session_id": runtime.session_id,
        "compressed": True,
        "replaced_count": compress_info["replaced_count"],
        "summary": compress_info["summary"],
        "messages": delivered,
        "usage": {
            "total_tokens": new_inputs.estimated_tokens,
            "context_window": new_inputs.ctx_length,
        },
    }


async def _do_clear_history(db: AsyncSession, conv: Conversation, runtime: RuntimeSession) -> dict[str, Any]:
    """清空会话所有消息（含 user / assistant / system / tool 各 role，保留会话行 + 写一条 status_cleared 标记）。

    返回 {"session_id", "cleared_count", "messages": [...]}。``cleared_count`` 是真正删除的行数。
    """
    total = (
        await db.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conv.id),
        )
    ).scalar_one()

    # 全量清掉本会话所有消息 + 视频附件，
    # 让 cleared 之后客户端只看到 status_cleared marker 一条历史行。
    await prune_videos_in_range(db, conv.id)
    await db.execute(delete(Message).where(Message.conversation_id == conv.id))

    marker = Message(
        conversation_id=conv.id,
        role="system",
        content=f"[🧹 会话已清空 — {total} 条消息]",
        subtype=MESSAGE_SUBTYPE_STATUS_CLEARED,
    )
    db.add(marker)
    await db.commit()

    delivered = await build_session_messages(conv.id, db)
    return {
        "session_id": runtime.session_id,
        "cleared_count": total,
        "messages": delivered,
    }


async def do_session_undo(
    user_id: int,
    session_id: str,
    source_message_id: int,
    *,
    runtime_sessions: dict[str, RuntimeSession] | None = None,
    dispatcher: Any | None = None,
) -> dict:
    """session.undo_to_message 的共享实现：业务校验 + per-conversation lock + in-flight guard + 服务调用 + 多窗口广播。

    透传 runtime_sessions / dispatcher 是为了 REST 与 WS 共用同一份安全网——REST 从 MANAGER 查表后传入，
    即可复用锁、in-flight 守卫与广播；不传时退化为「无 in-flight / 无广播」基础版（仅服务调用 + 锁）。
    """
    runtime = runtime_sessions.get(session_id) if runtime_sessions else None
    if runtime is not None and runtime.chat_task and not runtime.chat_task.done():
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "当前会话有正在生成的回复，请稍后再试")

    lock = _conversation_locks.setdefault(str(session_id), asyncio.Lock())
    async with lock, SESSION_LOCAL() as db:
        try:
            conv = await resolve_undo_target(db, user_id, session_id, source_message_id)
            await prune_videos_in_range(db, conv.id, lo=source_message_id)
            result = await undo_conversation_to_message(db, conv, source_message_id)
        except (UndoNotAllowedError, SourceNotFoundError) as e:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(e))

    if dispatcher is not None:
        await dispatcher.push_event(
            "message.deleted",
            {
                "session_id": session_id,
                "deleted_count": result["deleted_count"],
                "anchor": result["anchor"],
                "messages": result["messages"],
            },
            session_id=session_id,
        )

    logger.info(
        "session.undo_to_message",
        extra={
            "user_id": user_id,
            "session_id": session_id,
            "source_message_id": source_message_id,
            "deleted_count": result["deleted_count"],
        },
    )
    return result


@register_slash_command(
    name="clear",
    aliases=("清空", "清理", "reset"),
    description="清空当前会话消息（保留会话行）",
    requires_confirmation=True,
)
async def _slash_clear(ctx: SlashCommandContext) -> SlashCommandResult:
    """``/清理`` 命令 handler。``confirmed`` 由 ``command.dispatch`` 在调用前把关，未传则抛 SLASH_CONFIRM_REQUIRED。"""
    if ctx.runtime.chat_task and not ctx.runtime.chat_task.done():
        raise JsonRpcError(JSONRPC_SLASH_BUSY, "请先停止当前生成再清理会话")
    lock = _conversation_locks.setdefault(str(ctx.runtime.conversation_id), asyncio.Lock())
    async with lock, SESSION_LOCAL() as db:
        conv = await _find_owned_conv(db, ctx.user_id, ctx.session_id)
        if conv is None:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {ctx.session_id!r}")
        result = await _do_clear_history(db, conv, ctx.runtime)
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
    if ctx.runtime.chat_task and not ctx.runtime.chat_task.done():
        raise JsonRpcError(JSONRPC_SLASH_BUSY, "请先停止当前生成再压缩会话")
    lock = _conversation_locks.setdefault(str(ctx.runtime.conversation_id), asyncio.Lock())
    async with lock, SESSION_LOCAL() as db:
        conv = await _find_owned_conv(db, ctx.user_id, ctx.session_id)
        if conv is None:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {ctx.session_id!r}")
        result = await _do_compress_history(db, conv, ctx.user_id, ctx.runtime)
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

    norm_content = content[:MAX_RECALL_CONTENT_CHARS]
    context = normalize_recall_context("manual")
    tags = json.dumps(["user_preference"])
    importance = 1.5

    async with SESSION_LOCAL() as db:
        scope = await resolve_memory_scope(db, ctx.user_id, ctx.session_id)
        mem = await create_memory(
            db,
            scope,
            source=MemorySource("manual", session_id=int(ctx.session_id)),
            content=norm_content,
            context=context,
            tags=tags,
            importance=importance,
        )
        await db.commit()

    await backfill_memory_embeddings(scope, [EmbeddingItem(mem.id, mem.content, mem.content_version)])

    display_content = norm_content if len(norm_content) <= 60 else f"{norm_content[:57]}..."
    return SlashCommandResult(
        status="ok",
        message=f"已记住：{display_content}",
        hydrate=False,
        payload={"memory_id": mem.id, "content": norm_content},
    )


def _register_session_handlers(
    dispatcher: JsonRpcDispatcher,
    runtime_sessions: dict[str, RuntimeSession],
    llm_config: dict,
    user_id: int,
    replay_buffer: ReplayBuffer | None = None,
    user_session: UserGatewaySession | None = None,
) -> None:
    effective_buffer = replay_buffer or (user_session.replay_buffer if user_session else ReplayBuffer())

    def _mount_runtime(conv: Conversation, cwd: str | None, *, cancel_existing: bool = False) -> RuntimeSession:
        """取消同会话在内存中的 runtime，再挂载新的。"""
        for existing in list(runtime_sessions.values()):
            if existing.conversation_id == conv.id:
                if cancel_existing and existing.chat_task and not existing.chat_task.done():
                    existing.chat_task.cancel()
                if not cancel_existing:
                    return existing
                runtime_sessions.pop(existing.session_id, None)
        runtime = new_runtime_session(
            conversation_id=conv.id,
            cwd=cwd,
            settings_json=conv.settings_json,
            kind=conv.kind,
        )
        runtime_sessions[runtime.session_id] = runtime
        return runtime

    async def _runtime_info(cfg: dict[str, Any], runtime: RuntimeSession, conv: Conversation) -> dict[str, Any]:
        async with SESSION_LOCAL() as db:
            user_settings = await load_user_settings(db, user_id)
        effective = merge_session_settings(user_settings, runtime.settings, conv=conv)
        return runtime_info_snapshot(cfg, runtime) | {
            "settings": runtime.settings | asdict(resolve_inference_settings(effective, conv=conv)),
        }

    async def session_ack(params: dict) -> dict:
        seq = params.get("seq")
        if not _is_nonneg_int(seq):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "seq must be a non-negative int")
        pruned = effective_buffer.ack(seq)
        return {"acked": seq, "pruned": pruned}

    dispatcher.register("session.ack", session_ack)

    async def session_ping(_params: dict) -> dict:
        return {}

    dispatcher.register("session.ping", session_ping)

    async def _fetch_truncated_history(conv_id: int, db: AsyncSession) -> tuple[list[dict[str, Any]], bool, str | None]:
        head = await build_session_messages(
            conv_id,
            db,
            limit=SESSION_HISTORY_TRUNCATE_THRESHOLD + SESSION_HISTORY_PRE_BUFFER,
            desc=True,
            include_id=True,
        )
        head.reverse()
        truncated = len(head) > SESSION_HISTORY_TRUNCATE_THRESHOLD
        delivered = head[-SESSION_HISTORY_TRUNCATE_THRESHOLD:] if truncated else head
        next_cursor = str(delivered[0]["id"]) if truncated and delivered else None
        return delivered, truncated, next_cursor

    async def session_get_main(_params: dict) -> dict:
        async with SESSION_LOCAL() as db:
            conv = await get_or_create_special_conversation(db, user_id, "companion")
            delivered, truncated, next_cursor = await _fetch_truncated_history(conv.id, db)
        runtime = _mount_runtime(conv, conv.cwd)
        cfg = user_session.llm_config if user_session else llm_config
        await dispatcher.flush_unsent()
        return SessionResumeResult(
            session_id=runtime.session_id,
            message_count=len(delivered),
            messages=delivered,
            info=await _runtime_info(cfg, runtime, conv),
            current_seq=effective_buffer.max_seq,
            truncated=truncated,
            next_cursor=next_cursor,
        ).model_dump()

    dispatcher.register("session.get_main", session_get_main)

    async def session_create(params: dict) -> dict:
        cwd = params.get("cwd") or None
        raw_preset = params.get("system_preset_id")
        preset_id: str = "developer"
        if raw_preset is not None and raw_preset != "":
            if not isinstance(raw_preset, str) or raw_preset not in SYSTEM_PRESET_CATALOG:
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"system_preset_id must be one of {sorted(SYSTEM_PRESET_CATALOG)} or omitted",
                )
            preset_id = raw_preset
        async with SESSION_LOCAL() as db:
            conv = Conversation(user_id=user_id, cwd=cwd, system_preset_id=preset_id)
            db.add(conv)
            await db.commit()
            await db.refresh(conv)
        runtime = _mount_runtime(conv, cwd)
        logger.info(
            "session.create",
            extra={"user_id": user_id, "session_id": runtime.session_id, "cwd": cwd, "system_preset_id": preset_id},
        )
        cfg = user_session.llm_config if user_session else llm_config
        await dispatcher.flush_unsent()
        return SessionCreateResult(
            session_id=runtime.session_id,
            info=await _runtime_info(cfg, runtime, conv),
        ).model_dump()

    dispatcher.register("session.create", session_create)

    async def system_list_presets(_params: dict) -> dict:
        """返回内置系统预设的元数据清单（不含 body）。body 永远不下发到客户端。"""
        return PromptPresetListResponse(
            presets=[
                PromptPresetSummary(id=p.id, name=p.name, description=p.description, icon_key=p.icon_key)
                for p in SYSTEM_PRESET_CATALOG.values()
            ],
        ).model_dump()

    dispatcher.register("system.list_presets", system_list_presets)

    async def session_fork(params: dict) -> dict:
        """从用户拥有的源会话的某条消息派生新会话：复制 1..source_message_id 共 N 条消息到 kind='standard' 的新会话；新会话挂载 runtime 并返回 SessionResumeResult，客户端可直接 hydrate 并自动挂载。"""
        source_session_id = _require_str(params, "source_session_id")
        raw_id = params.get("source_message_id")
        if not _is_nonneg_int(raw_id):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "source_message_id must be a non-negative int")

        async with SESSION_LOCAL() as db:
            try:
                result = await fork_conversation_from_message(db, user_id, source_session_id, int(raw_id))
            except ForkNotAllowedError as e:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(e))
            except SourceNotFoundError as e:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, str(e))

        # 服务函数只负责 DB 落库；runtime 挂载与 info 在这里补，与 session.resume 路径一致
        conv_id = int(result["session_id"])
        async with SESSION_LOCAL() as db:
            conv = await _find_owned_conv(db, user_id, str(conv_id))
            if conv is None:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"forked session not found: {conv_id!r}")
        runtime = _mount_runtime(conv, conv.cwd)
        cfg = user_session.llm_config if user_session else llm_config
        await dispatcher.flush_unsent()
        logger.info(
            "session.fork",
            extra={
                "user_id": user_id,
                "source_session_id": source_session_id,
                "source_message_id": int(raw_id),
                "new_session_id": runtime.session_id,
                "message_count": result["message_count"],
            },
        )
        return SessionResumeResult(
            session_id=runtime.session_id,
            message_count=result["message_count"],
            messages=result["messages"],
            info=await _runtime_info(cfg, runtime, conv),
            resumed=False,
            replayed_count=0,
            current_seq=effective_buffer.max_seq,
            truncated=False,
            next_cursor=None,
        ).model_dump()

    dispatcher.register("session.fork", session_fork)

    async def session_resume(params: dict) -> dict:
        stored_id = _require_str(params, "session_id")
        last_seq = params.get("last_seq")
        after_id = params.get("after_id")
        if after_id is not None and not _is_nonneg_int(after_id):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "after_id must be a non-negative int")
        async with SESSION_LOCAL() as db:
            conv = await _find_owned_conv(db, user_id, stored_id)
            if conv is None:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"stored session not found: {stored_id!r}")

        cfg = user_session.llm_config if user_session else llm_config

        if isinstance(last_seq, int) and last_seq > 0 and effective_buffer.can_replay(last_seq):
            runtime = _mount_runtime(conv, conv.cwd, cancel_existing=False)
            replayed_frames = await dispatcher.replay(last_seq) or []
            logger.info(
                "session.resume replayed frames",
                extra={
                    "user_id": user_id,
                    "session_id": runtime.session_id,
                    "replayed": len(replayed_frames),
                    "last_seq": last_seq,
                },
            )
            return SessionResumeResult(
                session_id=runtime.session_id,
                message_count=0,
                messages=[],
                info=await _runtime_info(cfg, runtime, conv),
                resumed=True,
                replayed_count=len(replayed_frames),
                current_seq=effective_buffer.max_seq,
            ).model_dump()

        # 本地已有历史：锚点仍存在时只回增量，避免冷启动全量重拉。
        if after_id is not None and after_id > 0:
            async with SESSION_LOCAL() as db:
                anchor_exists = (
                    await db.execute(
                        select(Message.id).where(Message.id == after_id, Message.conversation_id == conv.id),
                    )
                ).scalar_one_or_none() is not None
                if anchor_exists:
                    delivered = await build_session_messages(conv.id, db, after_id=after_id, include_id=True)
            if anchor_exists:
                runtime = _mount_runtime(conv, conv.cwd, cancel_existing=True)
                await dispatcher.flush_unsent()
                logger.info(
                    "session.resume incremental",
                    extra={
                        "user_id": user_id,
                        "session_id": runtime.session_id,
                        "after_id": after_id,
                        "new_count": len(delivered),
                    },
                )
                return SessionResumeResult(
                    session_id=runtime.session_id,
                    message_count=len(delivered),
                    messages=delivered,
                    info=await _runtime_info(cfg, runtime, conv),
                    resumed=False,
                    replayed_count=0,
                    current_seq=effective_buffer.max_seq,
                    incremental=True,
                ).model_dump()

        # 客户端序列号失同步或超时，回退到 DB 历史防御性截断重水化
        async with SESSION_LOCAL() as db:
            delivered, truncated, next_cursor = await _fetch_truncated_history(conv.id, db)
        runtime = _mount_runtime(conv, conv.cwd, cancel_existing=True)
        await dispatcher.flush_unsent()
        logger.info("session.resume full reload", extra={"user_id": user_id, "session_id": runtime.session_id})
        return SessionResumeResult(
            session_id=runtime.session_id,
            message_count=len(delivered),
            messages=delivered,
            info=await _runtime_info(cfg, runtime, conv),
            resumed=False,
            replayed_count=0,
            current_seq=effective_buffer.max_seq,
            truncated=truncated,
            next_cursor=next_cursor,
        ).model_dump()

    async def session_interrupt(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        if runtime.chat_task and not runtime.chat_task.done():
            runtime.chat_task.cancel()
        return {}

    dispatcher.register("session.interrupt", session_interrupt)

    async def session_set_settings(params: dict) -> dict:
        sess_runtime = user_session.runtime_sessions if user_session else runtime_sessions
        runtime = _get_runtime(sess_runtime, params)
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
            settings = safe_json_loads(conv.settings_json, default={})
            settings = settings if isinstance(settings, dict) else {}
            for key, value in settings_patch.items():
                target = SESSION_TO_GLOBAL_KEY_ALIASES[key]
                for stored_key in list(settings):
                    if SESSION_TO_GLOBAL_KEY_ALIASES.get(stored_key, stored_key) == target:
                        del settings[stored_key]
                if value is not None:
                    settings[key] = value
            if settings_patch:
                conv.settings_json = json.dumps(settings, ensure_ascii=False)
                await db.commit()
            runtime.settings = settings
        cfg = user_session.llm_config if user_session else llm_config
        return {
            "session_id": runtime.session_id,
            "settings": dict(runtime.settings),
            "info": await _runtime_info(cfg, runtime, conv),
        }

    dispatcher.register("session.set_settings", session_set_settings)

    async def session_compress_context(params: dict) -> dict:
        sess_runtime = user_session.runtime_sessions if user_session else runtime_sessions
        runtime = _get_runtime(sess_runtime, params)
        if runtime.chat_task and not runtime.chat_task.done():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "当前会话有正在生成的回复，请稍后再试")

        async with SESSION_LOCAL() as db:
            conv = await _find_owned_conv(db, user_id, runtime.session_id)
            if conv is None:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {runtime.session_id!r}")
            return await _do_compress_history(db, conv, user_id, runtime)

    dispatcher.register("session.compress_context", session_compress_context)
    dispatcher.register("session.compress", session_compress_context)

    async def session_undo_to_message(params: dict) -> dict:
        """就地截断会话并把锚点载荷以 anchor 字段返回，供客户端落回输入框作为草稿。需要 ``confirmed=true``；in-flight 拒绝；仅 ``kind='standard'`` 允许。广播 ``message.deleted`` 事件给同 user 其他窗口。"""
        session_id = _require_str(params, "session_id")
        if not bool(params.get("confirmed")):
            raise JsonRpcError(
                JSONRPC_SLASH_CONFIRM_REQUIRED,
                "session.undo_to_message requires confirmed=true",
                data={"requires_confirmation": True},
            )
        raw_id = params.get("source_message_id")
        if not _is_nonneg_int(raw_id):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "source_message_id must be a non-negative int")

        sess_runtime = user_session.runtime_sessions if user_session else runtime_sessions
        return await do_session_undo(
            user_id,
            session_id,
            int(raw_id),
            runtime_sessions=sess_runtime,
            dispatcher=dispatcher,
        )

    dispatcher.register("session.undo_to_message", session_undo_to_message)

    async def session_clear_messages(params: dict) -> dict:
        """直接 RPC：清空当前会话的所有消息（保留会话行），写一条 status_cleared marker。

        是 ``/清理`` slash 命令的底层副作用 RPC；提供 REST 镜像与未来自动化测试入口。
        需要 ``confirmed=true``（仅在与命令层联动时约定；这里强制所有调用都传 confirmed）。
        """
        sess_runtime = user_session.runtime_sessions if user_session else runtime_sessions
        runtime = _get_runtime(sess_runtime, params)
        if runtime.chat_task and not runtime.chat_task.done():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "当前会话有正在生成的回复，请稍后再试")
        if not bool(params.get("confirmed")):
            raise JsonRpcError(
                JSONRPC_SLASH_CONFIRM_REQUIRED,
                "session.clear_messages requires confirmed=true",
                data={"requires_confirmation": True},
            )
        async with SESSION_LOCAL() as db:
            conv = await _find_owned_conv(db, user_id, runtime.session_id)
            if conv is None:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {runtime.session_id!r}")
            result = await _do_clear_history(db, conv, runtime)
        logger.info(
            "session.clear_messages",
            extra={"user_id": user_id, "session_id": runtime.session_id, "cleared_count": result["cleared_count"]},
        )
        return result

    dispatcher.register("session.clear_messages", session_clear_messages)

    async def command_dispatch(params: dict) -> dict:
        """Slash 命令分发入口：按 ``command`` 字段查 SLASH_COMMANDS 注册表并执行对应 handler。

        返回 ``{command, result: SlashCommandResult.model_dump()}`` 形态；同步广播
        ``command.result`` 事件给所有订阅同 session 的窗口，便于多窗口场景同步渲染 pill。
        """
        sess_runtime = user_session.runtime_sessions if user_session else runtime_sessions
        runtime = _get_runtime(sess_runtime, params)
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
            dispatcher=dispatcher,
            args=args_list,
            raw=raw_command,
            confirmed=confirmed,
        )

        try:
            result = await cmd.handler(ctx)
        except JsonRpcError:
            raise
        except Exception:
            logger.exception("slash command handler failed", extra={"command": cmd.name, "user_id": user_id})
            raise JsonRpcError(JSONRPC_SLASH_GENERIC, f"command /{name} failed unexpectedly") from None

        result_payload = {
            "command": cmd.name,
            "result": {
                "status": result.status,
                "message": result.message,
                "payload": result.payload,
                "hydrate": result.hydrate,
            },
        }
        await dispatcher.push_event("command.result", result_payload, session_id=runtime.session_id)
        return result_payload

    dispatcher.register("command.dispatch", command_dispatch)

    async def command_list(_params: dict) -> dict:
        """列出可用 slash 命令元数据；供客户端 ``/帮助`` 或调试面板使用。"""
        return {"commands": list_commands_for_user()}

    dispatcher.register("command.list", command_list)

    def _track(task: asyncio.Task) -> None:
        if user_session is not None:
            user_session.background_tasks.add(task)
            task.add_done_callback(user_session.background_tasks.discard)

    async def prompt_submit(params: dict) -> dict:
        sess_runtime = user_session.runtime_sessions if user_session else runtime_sessions
        runtime = _get_runtime(sess_runtime, params)
        # im 会话由通道桥独占写入（外部 IM 消息驱动回合），桌面端只读旁观历史。
        if runtime.kind == IM_KIND:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "IM 会话由通道桥接维护，仅只读")
        if runtime.chat_task and not runtime.chat_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(runtime.chat_task), timeout=0.3)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                logger.debug("chat_task shield wait timed out for session %s", runtime.session_id)
            if runtime.chat_task and not runtime.chat_task.done():
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"session {runtime.session_id!r} already has an in-flight turn",
                )

        # 跨门：companion 反应正在该用户主会话上空跑。
        if any(uid == user_id for uid, _ in _inflight_interact):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "companion reaction in-flight; please retry after it lands")

        await interrupt_user_event_tasks(user_id, CRON_TURN_EVENT)

        truncate_ordinal = params.get("truncate_before_user_ordinal")
        if truncate_ordinal is not None and not _is_nonneg_int(truncate_ordinal):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "truncate_before_user_ordinal must be a non-negative int")
        if truncate_ordinal is not None:
            async with SESSION_LOCAL() as db:
                user_total = (
                    await db.execute(
                        select(func.count(Message.id)).where(
                            Message.conversation_id == runtime.conversation_id,
                            Message.role == "user",
                        ),
                    )
                ).scalar_one()
                if truncate_ordinal < 0 or truncate_ordinal >= user_total:
                    raise JsonRpcError(
                        JSONRPC_INVALID_PARAMS,
                        f"truncate_before_user_ordinal {truncate_ordinal} no longer in session history",
                    )
                nth = (
                    await db.execute(
                        select(Message.id)
                        .where(
                            Message.conversation_id == runtime.conversation_id,
                            Message.role == "user",
                        )
                        .order_by(Message.id)
                        .offset(truncate_ordinal)
                        .limit(1),
                    )
                ).scalar_one()
                # 即将删除的行所引用的视频是死重量：删行前先清文件（行本身随之删除，无需改写占位）。
                await prune_videos_in_range(db, runtime.conversation_id, lo=nth)
                await db.execute(
                    delete(Message).where(
                        Message.conversation_id == runtime.conversation_id,
                        Message.id >= nth,
                    ),
                )
                db.expire_all()
                await db.commit()

        precursor_user_message_ids: list[int] = []
        batch = params.get("batch")
        if batch is not None:
            if not isinstance(batch, list) or not batch:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "batch must be a non-empty list")
            validated_batch = []
            for item in batch:
                if not isinstance(item, dict):
                    raise JsonRpcError(JSONRPC_INVALID_PARAMS, "each item in batch must be an object")
                t = _require_str(item, "text")
                att = _validate_attachments(item, runtime.session_id)
                validated_batch.append({"text": t, "attachments": att})

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

        reset_user_outreach(user_id)
        note_user_contact(user_id)

        req = ChatRequest(
            session_id=runtime.session_id,
            message=ChatMessageRequest(role="user", content=text, attachments=attachments),
        )

        disp = user_session.dispatcher if user_session else dispatcher
        emitter = JsonRpcEmitter(raw=None, dispatcher=disp, session_id=runtime.session_id)

        cur_cfg = user_session.llm_config if user_session else llm_config
        cur_ctx = user_session.session_client_context if user_session else None

        async def _run_turn() -> None:
            _inflight_prompt.add(user_id)
            try:
                try:
                    await run_chat_turn(
                        req,
                        cur_cfg,
                        user_id,
                        emitter,
                        session_client_context=cur_ctx,
                        track_task=_track,
                        session_settings=runtime.settings,
                        precursor_user_message_ids=precursor_user_message_ids or None,
                    )
                except (WebSocketDisconnect, asyncio.CancelledError):
                    raise
                except Exception as e:
                    logger.exception("prompt.submit chat_turn failed")
                    with contextlib.suppress(Exception):
                        await disp.push_error_event(str(e), session_id=runtime.session_id)
            finally:
                _inflight_prompt.discard(user_id)

        runtime.chat_task = asyncio.create_task(_run_turn())
        _track(runtime.chat_task)
        return {"queued": True}

    dispatcher.register("prompt.submit", prompt_submit)

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

    dispatcher.register("tool.result", tool_result_handler)

    async def tools_sync(params: dict) -> dict:
        tools = params.get("tools", [])
        if not isinstance(tools, list):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools must be a list")
        REGISTRY.update_runner_tools(user_id, tools, skill_scope_version=params.get("skill_scope_version", 0))
        return ToolsSyncResult(count=len(tools)).model_dump()

    dispatcher.register("tools.sync", tools_sync)

    async def image_attach(params: dict) -> dict:
        # 路径模式：后端不读字节，LLM 通过 Runner 文件工具读取。
        path = _require_str(params, "path")
        return path_attach_ref(path)

    dispatcher.register("session.create", session_create)
    dispatcher.register("session.resume", session_resume)
    dispatcher.register("session.interrupt", session_interrupt)
    dispatcher.register("image.attach", image_attach)

    async def companion_set_timezone(params: dict) -> dict:
        # Desktop 每次连接上报本地 IANA 时区：夜间批处理与互动统计都按用户本地日聚合，
        # 缺这一行时整个夜间流水线（画像/整理/规划/日记）会静默跳过。
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

    dispatcher.register("companion.set_timezone", companion_set_timezone)

    async def companion_check_affect(params: dict) -> dict:
        # desktop idle 监视器在阈值+冷却后调用；LLM 决策是否发出 companion.affect 切换到情境情绪（无气泡、无 TTS）。
        # 具身情境推理只服务自主档下的桌面精灵；客户端另以表面可见性拦截，服务端在档位边界兜底非官方调用。
        if await get_disturbance_tier(user_id) != "autonomous":
            return {"emotion": None, "actions": [], "reason": "autonomous tier required"}
        now = time.monotonic()
        if _user_throttled(_last_check_affect_ts, user_id, CHECK_AFFECT_MIN_INTERVAL_SECONDS, now):
            logger.debug(
                "check_affect: throttled",
                extra={"user_id": user_id, "since_sec": round(now - _last_check_affect_ts.get(user_id, 0.0), 3)},
            )
            return {"emotion": None, "reason": "throttled"}
        _last_check_affect_ts[user_id] = now

        idle_seconds = coerce_non_negative_float(params.get("idle_seconds"))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        cfg = user_session.llm_config if user_session else llm_config
        return await check_affect(user_id, idle_seconds, local_hour, cfg)

    dispatcher.register("companion.check_affect", companion_check_affect)

    async def companion_record_interaction_stats(params: dict) -> dict:
        # poke / chat_turn 每事件统计供每日 Memory 汇总用，无 LLM 开销；desktop 侧合并到 STATS_THRESHOLD 后切分钟级节流。
        # hour 是用户本地小时（客户端上报 getHours()），与本地日期键同口径，夜间反思按本地日读取。
        kind = params.get("kind")
        hour = params.get("hour")
        if not isinstance(hour, int) or not 0 <= hour <= 23:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "hour must be int in [0, 23]")
        if kind not in ("poke", "chat_turn"):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"kind must be one of poke/chat_turn, got {kind!r}")
        return await record_interaction(user_id, kind, hour)

    dispatcher.register("companion.record_interaction_stats", companion_record_interaction_stats)

    async def companion_interact(params: dict) -> dict:
        # PROTOCOL §1.4：kind 支持 poke/pet/dizzy 三类语义（摸头、眩晕与戳击同级走 LLM 反应）。
        kind = params.get("kind")
        if kind not in ("poke", "pet", "dizzy"):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"kind must be one of poke/pet/dizzy, got {kind!r}")

        # 戳摸即「理了伙伴」——被节流拦掉的也算接触，刷新常规档被冷落问候的计时起点。
        note_user_contact(user_id)

        now = time.monotonic()
        if _user_throttled(_last_interact_ts, user_id, INTERACT_MIN_INTERVAL_SECONDS, now):
            return {"text": None, "emotion": None, "reason": "throttled"}

        # 跨门：renderer 发起的 chat turn 正在主会话上空跑——戳一戳可能在 in-flight 用户消息入库前写入 status_interaction 行，或 status_reaction 落在还在生成的助手回复前；按 throttled 契约静默丢弃。
        if user_id in _inflight_prompt:
            return {"text": None, "emotion": None, "reason": "user_busy"}

        user_cooldowns = _llm_cooldown_until.setdefault(user_id, {})
        if now < user_cooldowns.get(kind, 0.0):
            return {"text": None, "emotion": None, "reason": "rate_limited"}

        # per-(user, kind) 去重 in-flight 调用：慢的 LLM 响应不该让第二个反应请求溜过去。
        inflight_key = (user_id, kind)
        if inflight_key in _inflight_interact:
            return {"text": None, "emotion": None, "reason": "inflight"}
        _inflight_interact.add(inflight_key)
        # anti-dup 节流总是消费；成本窗口按结果分级——成功 5 分钟、失败 60 秒
        _last_interact_ts[user_id] = now

        poke_count = coerce_non_negative_int(params.get("poke_count"))
        idle_seconds = float(coerce_non_negative_float(params.get("idle_seconds")))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        region = params.get("region")
        if region is not None and not isinstance(region, str):
            region = None
        cfg = user_session.llm_config if user_session else llm_config

        try:
            res = await interact(user_id, kind, poke_count, idle_seconds, local_hour, cfg, region=region)
        finally:
            _inflight_interact.discard(inflight_key)

        if res.text is None:
            # 失败也封 60s：LLM 调用已付过钱，故障模式下成本闸门同样要生效
            user_cooldowns[kind] = now + INTERACT_FAILURE_COOLDOWN_SECONDS
            return res.model_dump()

        # 只有用户实际看到的反应才值得写历史行——未应答的戳一戳否则会污染主会话。
        # 痕迹行按 kind 记不同的动作描述（poke 带区域细分）。
        if kind == "pet":
            action_name = "（摸了摸精灵的头）"
        elif kind == "dizzy":
            action_name = "（把精灵晃晕了）"
        else:
            action_name = "（戳了戳精灵）"
            if region and (region_zh := REGION_NAMES_ZH.get(region)):
                action_name = f"（戳了戳精灵的{region_zh}）"
        await _record_main_conversation(user_id, "user", action_name, "status_interaction")
        await _record_main_conversation(user_id, "assistant", res.text, "status_reaction")
        # 不论 DB 结果如何都消耗完整冷却：LLM 调用已经付过钱，持久化失败不该为第二次调用打开门。
        user_cooldowns[kind] = now + USER_TRIGGERED_LLM_COOLDOWN_SECONDS
        return res.model_dump()

    dispatcher.register("companion.interact", companion_interact)

    async def companion_should_act(params: dict) -> dict:
        kind = params.get("kind", "periodic_provision")
        if kind not in ("periodic_provision",):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"invalid kind {kind!r}")

        # 空间自主决策只服务自主档；客户端另以桌面精灵可见性拦截。
        if await get_disturbance_tier(user_id) != "autonomous":
            return {"should_act": False, "action": "stay", "reason": "autonomous tier required"}

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
        cfg = user_session.llm_config if user_session else llm_config

        res = await should_act(
            user_id=user_id,
            kind=kind,
            idle_seconds=idle_seconds,
            local_hour=local_hour,
            focused_category=focused_category,
            fullscreen=fullscreen,
            screen_locked=screen_locked,
            seconds_since_last_action=seconds_since_last_action,
            llm_config=cfg,
        )
        # 走过去搭话（DESIGN §3.5/§6.4）：开场白经 companion.message 通道独立投递，
        # 客户端边走边说；RPC 响应只承载走位动作。text 的规范化已在 should_act 内完成，
        # 这里对空文本做防御性跳过（should_act 对无文本的 approach 已降级 stay）。
        if res.action == "approach" and isinstance(res.params, dict) and str(res.params.get("text") or "").strip():
            await emit_companion_message(
                user_id,
                str(res.params["text"]).strip(),
            )
        return res.model_dump()

    dispatcher.register("companion.should_act", companion_should_act)

    async def companion_get_user_profile(_params: dict) -> dict:
        # record_user_profile 的逆操作：retune 向导在打开前调用，预填它的 user_* 步骤。
        async with SESSION_LOCAL() as db:
            return await read_user_profile(db, MemoryScope(user_id, "companion"))

    dispatcher.register("companion.get_user_profile", companion_get_user_profile)

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
                    tag=tag if isinstance(tag, str) else None,
                    q=q if isinstance(q, str) else None,
                    limit=int(limit) if isinstance(limit, int) else 100,
                )
                counts = await memory_counts(db, scope)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))
        return {
            "memories": rows,
            "counts": counts,
            "system_preset_id": scope.system_preset_id,
            "session_id": params.get("session_id"),
        }

    async def memory_update(params: dict) -> dict:
        memory_id = params.get("memory_id")
        content = params.get("content")
        if not isinstance(memory_id, int) or not isinstance(content, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "memory_id (int) and content (str) required")
        try:
            async with SESSION_LOCAL() as db:
                scope = await _memory_scope(params, db)
            row = await update_memory(scope, memory_id, content=content)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))
        if row is None:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"memory {memory_id} not found")
        return row

    async def memory_delete(params: dict) -> dict:
        memory_id = params.get("memory_id")
        if not isinstance(memory_id, int):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "memory_id (int) required")
        async with SESSION_LOCAL() as db:
            scope = await _memory_scope(params, db)
            ok = await delete_memory(db, scope, memory_id)
        return {"deleted": ok}

    dispatcher.register("memory.list", memory_list)
    dispatcher.register("memory.update", memory_update)
    dispatcher.register("memory.delete", memory_delete)

    async def onboarding_get_state(_params: dict) -> dict:
        # desktop 启动时拉取 onboarding 进度；persona 定稿后 complete: true，desktop 跳过 onboarding。
        async with SESSION_LOCAL() as db:
            return await get_onboarding_state(db, user_id)

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
                return await submit_onboarding_field(db, user_id, field, value)
            except PersonaValidationError as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))

    dispatcher.register("onboarding.get_state", onboarding_get_state)
    dispatcher.register("onboarding.submit", onboarding_submit)

    async def avatar_regenerate(params: dict) -> dict:
        # 10-60s 同步生图以后台 task 跑，立即返回 queued: true 不阻塞 WS 接收循环；结果通过 avatar.regenerated 事件回。
        feedback = params.get("feedback")
        if feedback is not None and not isinstance(feedback, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "feedback must be a string")
        async with SESSION_LOCAL() as db:
            persona = await get_or_create_persona(db, user_id)
            if not persona.is_complete:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "finish onboarding before regenerating avatar")
            # DESIGN §5.4 形象锁定：确认后重生路径关闭——即时拒绝而非后台任务失败
            await raise_if_image_sealed(db, user_id, persona)
        job_id = f"avatar_regen_{user_id}_{secrets.token_urlsafe(6)}"
        lock = get_avatar_job_lock(user_id)
        if lock.locked():
            # 该用户上一轮 regen 仍在跑：desktop UI 是乐观的，告诉它这次请求排在活动请求之后，避免跨周期抢占。
            return {"queued": False, "job_id": job_id, "reason": "already_running"}

        async def _run() -> None:
            async with lock:
                try:
                    regen_busy = False
                    async with SESSION_LOCAL() as probe_db:
                        try:
                            got = (
                                await probe_db.execute(
                                    text("SELECT pg_try_advisory_xact_lock(:k)"),
                                    {"k": _AVATAR_REGEN_ADVISORY_NAMESPACE + int(user_id)},
                                )
                            ).scalar()
                            regen_busy = not bool(got)
                        except Exception:
                            regen_busy = False

                    if regen_busy:
                        payload = {"job_id": job_id, "error": "伙伴正在生成形象，请稍候"}
                    else:
                        asset = await regenerate_avatar(user_id=user_id, feedback=feedback)
                        payload = {"job_id": job_id, "asset_url": asset.asset_url, "id": asset.id}
                except AvatarGenerationError as exc:
                    logger.warning("avatar regenerate failed", extra={"user_id": user_id, "error": exc.internal})
                    payload = {"job_id": job_id, "error": str(exc)}
                except Exception:
                    logger.exception("avatar regenerate unexpected failure", extra={"user_id": user_id})
                    payload = {"job_id": job_id, "error": "伙伴形象生成失败，请稍后重试"}
            try:
                await dispatcher.push_event("avatar.regenerated", payload, session_id=None)
            except Exception:
                logger.debug("avatar.regenerated event push failed", extra={"user_id": user_id}, exc_info=True)

        task = asyncio.create_task(_run())
        if user_session is not None:
            user_session.background_tasks.add(task)
            task.add_done_callback(user_session.background_tasks.discard)
        else:
            # 双保险：尚未建立 per-user 会话时回落到模块级 set，确保 task 在完成前仍有强引用。
            _avatar_regen_tasks.add(task)
            task.add_done_callback(_avatar_regen_tasks.discard)
        return {"queued": True, "job_id": job_id}

    dispatcher.register("avatar.regenerate", avatar_regenerate)

    async def companion_model_retry_download(params: dict) -> dict:
        # 已付费 3D 结果的下载恢复：在 web 进程内调 request_model_download_retry，能力链重新驱动到 SPEC 校验。
        model_id = params.get("model_id")
        if not _is_nonneg_int(model_id) or model_id <= 0:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "model_id must be a positive int")
        async with SESSION_LOCAL() as db:
            try:
                model = await request_model_download_retry(db, user_id=user_id, model_id=model_id)
            except ModelGenerationError as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return {"model_id": model.id, "status": model.status}

    dispatcher.register("companion.model.retryDownload", companion_model_retry_download)

    async def tts_list_voices(params: dict) -> dict:
        # 语音目录（plan §3.5 / §6）。可选 language 过滤——未知值直接返回完整目录，避免将来新增 tag 时 400。
        language = params.get("language")
        if language is not None and not isinstance(language, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "language must be a string")
        language = normalize_voice_language(language)
        async with SESSION_LOCAL() as db:
            return (await list_tts_voices(db, user_id, language=language)).model_dump()

    async def tts_match_voice(params: dict) -> dict:
        # 把自由文本语音偏好映射到已配置供应商目录中的具体 voice id；onboarding 不为已有目录覆盖的窄标签任务付 LLM 延迟。
        preference = params.get("preference")
        if not isinstance(preference, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "preference must be a string")
        language = params.get("language")
        if language is not None and not isinstance(language, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "language must be a string")
        language = normalize_voice_language(language)
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
        async with SESSION_LOCAL() as db:
            try:
                result = await design_voice(db, user_id, prompt, preview_text=preview_text)
            except (ValueError, MissingLlmConfigError) as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return {
            "provider": result.provider,
            "voice_id": result.voice_id,
            "trial_audio_base64": base64.b64encode(result.trial_audio).decode("ascii"),
            "trial_audio_mime": result.trial_audio_mime,
        }

    dispatcher.register("tts.list_voices", tts_list_voices)
    dispatcher.register("tts.match_voice", tts_match_voice)
    dispatcher.register("tts.design_voice", tts_design_voice)
