import asyncio
import hashlib
import json
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from components import SETTINGS, get_logger, resolve_prompt_text, session_scope
from modules.auth import ChatRequestClientContext
from modules.channels import ChannelBinding, ChannelDelivery, ChannelDeliveryPayload, ChannelPeer
from modules.conversation import Message
from modules.system import ChatMessageRequest, ChatRequest
from modules.ws import COMPANION_TURN_EVENT, emit_ws_event
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.chat import load_user_settings, persist_queued_inbound_message, run_chat_turn
from services.domains.companion import note_user_contact
from services.infrastructure.desktop import MANAGER
from services.infrastructure.event_store import interrupt_user_event_tasks
from services.infrastructure.llm import resolve_user_llm_config
from services.infrastructure.tool_runtime import REGISTRY

from .base import ChannelAdapter, ChannelBindingSnapshot, InboundMessage
from .conversation import get_or_create_channel_conversation
from .formatting import chunk_text, strip_markdown
from .registry import resolve as _resolve_channel

logger = get_logger(__name__)

# 未知对端的首条消息触发的固定配对回复（pending 态只发一次）；放行由主人在 Hub / REST 审批。
PAIRING_NOTICE = "（对方尚未通过配对审批，已请主人确认；批准后再继续对话）"

# 回合已产出但渠道投递全失败时的兜底提示：不静默，让对端知道伙伴方才说话了。
_DELIVERY_FAILED_FALLBACK = "（伙伴刚想说话，但消息没能送达到这个渠道，请稍后再试）"

# 队列容量不足时的明确拒收提示：消息不入队也不落库，对端知道这条没被接收、可重发。
_QUEUE_FULL_NOTICE = "（正在处理的任务比较多，这条消息没有被接收；请稍后再发一次）"

# 中止指令白名单。必须**全等**比较而非包含匹配——「请不要停下来」「去停车场」都含「停」，
# 模糊匹配会把正常发言吞成中断。已审批对端等同本人、可驱动本机终端，中止是唯一的刹车。
_STOP_COMMANDS = frozenset({"停", "停止", "停下", "stop", "/stop"})

_STOP_ACK = "（已停止本轮回复）"

# 桌面连接状态作为环境事实注入 IM 回合。离线时 runner 工具会被整体清出注册表；提示只报告可验证的
# “当前未连接”，不猜测设备是否关机、网络是否中断等具体原因。
_DESKTOP_ONLINE_TEXTS = {
    "zh": "【环境】用户的电脑当前在线，你可以使用本机工具（读写文件、终端、浏览器等）帮他做事。",
    "en": "[Environment] The user's desktop is online; you can use the local tools (files, terminal, browser, etc.) to act on it.",
}

_DESKTOP_OFFLINE_TEXTS = {
    "zh": "【环境】用户的桌面端当前未连接，本机文件、终端和浏览器工具不可用。需要操作电脑时，只说明目前无法触达设备；不要猜测是关机、断网或其他原因，也不要假装操作已经完成。",
    "en": "[Environment] The user's desktop is currently disconnected, so local file, terminal, and browser tools are unavailable. If computer access is required, state only that the device cannot be reached now; do not guess whether it is powered off or why the connection failed, and never pretend the action completed.",
}

# 入站闸门串行化：state 读写与单飞行转移必须原子。
_STATE_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class _QueuedMessage:
    msg: InboundMessage
    message_id: int
    future: asyncio.Future[str | None]


@dataclass
class _ChannelState:
    """每绑定一个桥接状态：单飞行锁 + 排队队列（回合进行中到达的消息合并进后续回合的前导批）。

    单飞行由 ``task is not None`` 单独表达——另设一个 in_flight 布尔会与它成为同一事实的两份副本，
    任何一处漏改都会让绑定永久卡住（排队但无人消费）或让中止落空。
    """

    queue: deque[_QueuedMessage] = field(default_factory=deque)
    # peer_id → 时间戳 deque：进程内每分钟滑动窗，渠道侧无频控前的成本护栏。
    rate_window: dict[str, deque[float]] = field(default_factory=dict)
    # 接收段（落库 + 入队）串行化锁：保证落库序与入队序都等于渠道投递序。
    intake_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    delivery_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 进行中回合的 task 句柄，供中止指令取消；非 None 即代表单飞行占用中。
    task: asyncio.Task | None = None
    # 发起当前回合的对端——只有它能中止该回合。
    owner_peer_id: str | None = None
    # stop 已取消当前 task、但 task 仍在 finally 收尾。保留句柄用于所有权校验，并吞掉重复 stop。
    cancelling: bool = False


_STATES: dict[int, _ChannelState] = {}


def _state_for(binding_id: int) -> _ChannelState:
    return _STATES.setdefault(binding_id, _ChannelState())


def _rate_exceeded(state: _ChannelState, peer_id: str) -> bool:
    now = asyncio.get_running_loop().time()
    window = state.rate_window.setdefault(peer_id, deque())
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= SETTINGS.channels_inbound_rate_per_minute:
        return True
    window.append(now)
    return False


def _resolved(value: str | None) -> asyncio.Future[str | None]:
    fut: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
    fut.set_result(value)
    return fut


def _drain_queue(state: _ChannelState) -> None:
    """清空排队并 resolve 各 future——中止时必须做两件事，缺一不可。

    只取消当前 task 而不清队列，_run_turn 的 finally 会立刻起下一批，与中断意图相反；
    不 resolve future 则 handle_inbound 的 await 方（REST 调试通道）会永久挂起。
    被清出的消息已在接收阶段落库（queued 行），会在下一轮回合被收编消费，不会静默丢失。
    """
    while state.queue:
        queued = state.queue.popleft()
        if not queued.future.done():
            queued.future.set_result(None)


async def stop_binding_turns(binding_id: int) -> None:
    """停止并等待一个绑定的当前回合，清空队列且落地所有等待方。"""
    async with _STATE_LOCK:
        state = _STATES.pop(binding_id, None)
        if state is None:
            return
        _drain_queue(state)
        task = state.task
        state.task = None
        state.owner_peer_id = None
        state.cancelling = False
        if task is not None and not task.done():
            task.cancel()
    if task is not None and task is not asyncio.current_task():
        await asyncio.gather(task, return_exceptions=True)


async def _get_peer(db: AsyncSession, binding_id: int, peer_id: str) -> ChannelPeer | None:
    return (
        await db.execute(
            select(ChannelPeer).where(ChannelPeer.binding_id == binding_id, ChannelPeer.peer_id == peer_id),
        )
    ).scalar_one_or_none()


async def _emit_peer_request(user_id: int, channel: str, msg: InboundMessage) -> None:
    """待审批对端事件走 outbox（桌面离线暂存重投），驱动 Hub/通知侧的审批入口。"""
    async with session_scope() as db:
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="channel.peer_request",
            payload={"channel": channel, "peer_id": msg.peer_id, "peer_name": msg.peer_name, "preview": msg.text[:64]},
        )
        await db.commit()


def _dedup_key(snapshot: ChannelBindingSnapshot, msg: InboundMessage) -> str | None:
    if not msg.msg_id:
        return None
    identity = json.dumps([snapshot.id, snapshot.channel, msg.peer_id, msg.msg_id], ensure_ascii=False)
    return hashlib.sha256(identity.encode()).hexdigest()


async def handle_inbound(adapter: ChannelAdapter, msg: InboundMessage) -> asyncio.Future[str | None]:
    """入站闸门：白名单检查 → 主动状态联动 → 接收落库入队；返回 per-message future（回合完成时以回复 resolve）。

    future 由调用方决定等待方式：REST 端点（用于无外部 IM 接入时的链路验证）await 到拿回复；轮询型适配器（微信 iLink）fire-and-forget。
    """
    snapshot = adapter.snapshot
    async with session_scope() as db:
        binding = await db.get(ChannelBinding, snapshot.id)
        if binding is None:
            return _resolved(None)
        peer = await _get_peer(db, binding.id, msg.peer_id)
        first_pending = peer is None
        if peer is None:
            peer = ChannelPeer(binding_id=binding.id, peer_id=msg.peer_id, peer_name=msg.peer_name, status="pending")
            db.add(peer)
        else:
            peer.peer_name = msg.peer_name or peer.peer_name
        peer.last_message_at = datetime.now(UTC)
        try:
            await db.commit()
        except IntegrityError:
            # 并发首条同 peer：判负方回滚后重读胜者行，按既有对端继续走闸门（配对回复只由胜者发一次）。
            await db.rollback()
            peer = await _get_peer(db, binding.id, msg.peer_id)
            if peer is None:
                return _resolved(None)
            first_pending = False
        peer_status = peer.status
        dedup_key = _dedup_key(snapshot, msg)
        if peer_status == "allowed" and dedup_key is not None:
            existing_id = (
                await db.execute(select(Message.id).where(Message.dedup_key == dedup_key))
            ).scalar_one_or_none()
            if existing_id is not None:
                return _resolved(None)

    if peer_status != "allowed":
        if peer_status == "pending" and first_pending:
            # 默认拒绝 + 一次性配对提示：既不让陌生人消耗 LLM 回合，也不完全冷拒。
            try:
                await adapter.send_text(msg.peer_id, PAIRING_NOTICE, msg.context_token)
            except Exception:
                logger.exception("pairing notice delivery failed", extra={"binding": snapshot.id, "peer": msg.peer_id})
            await _emit_peer_request(snapshot.user_id, snapshot.channel, msg)
            return _resolved(PAIRING_NOTICE)
        return _resolved(None)

    # IM 用户消息同样优先中断主动回合、刷新接触计时——与 prompt.submit 同一契约。
    note_user_contact(snapshot.user_id)
    await interrupt_user_event_tasks(snapshot.user_id, COMPANION_TURN_EVENT)

    state = _state_for(snapshot.id)

    # 中止判定必须在频控之前：用户眼看伙伴在电脑上做错事时往往连发几条，正好把窗口打满，
    # 此时若先频控就把唯一的刹车一起丢掉了。中止不消耗窗口配额。
    if msg.text.strip().lower() in _STOP_COMMANDS:
        stopped = False
        consumed = False
        async with _STATE_LOCK:
            # 只有发起该回合的对端能中止它——多对端绑定下，别人的回合不该被旁人叫停。
            # 空闲态不误触：没有进行中的回合时「停」就是一句普通话，照常走回合。
            if (task := state.task) is not None and state.owner_peer_id == msg.peer_id:
                consumed = True
                if not state.cancelling:
                    _drain_queue(state)
                    # 句柄保留到 task 自己完成清理；旧 task 无权清除期间可能接管的新 task。
                    state.cancelling = True
                    task.cancel()
                    stopped = True
        # 确认在锁外发：cancel 只是排程，被取消回合的 finally 还要抢同一把锁，
        # 在锁内 await 网络投递会把它一直挡住。
        if stopped:
            try:
                await adapter.send_text(msg.peer_id, _STOP_ACK, msg.context_token)
            except Exception:
                logger.exception("stop ack delivery failed", extra={"binding": snapshot.id})
            return _resolved(_STOP_ACK)
        if consumed:
            return _resolved(None)

    if _rate_exceeded(state, msg.peer_id):
        logger.warning("inbound rate limit exceeded, dropping", extra={"binding": snapshot.id, "peer": msg.peer_id})
        return _resolved(None)

    future = await _intake_message(adapter, state, msg)
    # 重投消息没有新鲜回复上下文，不消耗补发尝试次数。
    if not future.done() or future.result() is not None:
        _schedule_delivery_flush(adapter, state, msg.peer_id, msg.context_token)
    return future


async def _intake_message(
    adapter: ChannelAdapter,
    state: _ChannelState,
    msg: InboundMessage,
) -> asyncio.Future[str | None]:
    """接收段：容量校验 → 先持久化（queued 行 + 去重）→ 单飞行入队。

    intake 锁串行化同一绑定的接收段，保证落库序与入队序都等于渠道投递序。容量不足在落库
    **之前**明确拒收——已确认接收（落库）的消息不静默丢弃；被拒收的消息不落库，对端可重发。
    """
    snapshot = adapter.snapshot
    future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
    async with state.intake_lock:
        async with _STATE_LOCK:
            busy = state.task is not None and len(state.queue) >= SETTINGS.channels_turn_queue_max
        if busy:
            dedup_key = _dedup_key(snapshot, msg)
            if dedup_key is not None:
                async with session_scope() as db:
                    existing_id = (
                        await db.execute(select(Message.id).where(Message.dedup_key == dedup_key))
                    ).scalar_one_or_none()
                if existing_id is not None:
                    future.set_result(None)
                    return future
            logger.warning("turn queue full; rejecting inbound", extra={"binding": snapshot.id, "peer": msg.peer_id})
            try:
                await adapter.send_text(msg.peer_id, _QUEUE_FULL_NOTICE, msg.context_token)
            except Exception:
                logger.exception("queue-full notice delivery failed", extra={"binding": snapshot.id})
            future.set_result(_QUEUE_FULL_NOTICE)
            return future

        base = SETTINGS.public_base_url.strip().rstrip("/")
        attachments = [
            {
                "type": a.type,
                "file_url": f"{base}{a.url}" if base and a.url.startswith("/api/media/files/") else a.url,
                **({"name": a.name} if a.name else {}),
            }
            for a in msg.attachments
        ]
        async with session_scope() as db:
            binding = await db.get(ChannelBinding, snapshot.id)
            if binding is None:
                future.set_result(None)
                return future
            conv = await get_or_create_channel_conversation(
                db,
                binding,
                title_override=_resolve_channel(binding.channel).conversation_title,
            )
            row = await persist_queued_inbound_message(
                db,
                conv.id,
                text=msg.text,
                attachments=attachments,
                dedup_key=_dedup_key(snapshot, msg),
            )
            if row is None:
                # 渠道重投的重复消息：同标识行已存在，不再入队或回复。
                logger.info("duplicate inbound dropped", extra={"binding": snapshot.id, "peer": msg.peer_id})
                future.set_result(None)
                return future

        item = _QueuedMessage(msg=msg, message_id=row.id, future=future)
        async with _STATE_LOCK:
            if _STATES.get(snapshot.id) is not state:
                future.set_result(None)
                return future
            if state.task is not None:
                state.queue.append(item)
            else:
                state.owner_peer_id = msg.peer_id
                state.task = asyncio.create_task(_run_turn(adapter, state, [item]), name=f"channels.turn.{snapshot.id}")
    return future


class ChannelTurnEmitter:
    """无头回合发射器：捕获终端回复并转发 typing，工具中间轮不送出正文。"""

    def __init__(self) -> None:
        self.reply_text: str | None = None
        self.error: str | None = None
        self.media: list[dict] = []
        self._on_start: Callable[[], Awaitable[None]] | None = None
        self._typing_task: asyncio.Task | None = None

    def _clear_typing_task(self, task: asyncio.Task) -> None:
        # 仅当仍是当前 task 时清空：新一轮 typing 已覆盖时，旧回调安全跳过。
        if self._typing_task is task:
            self._typing_task = None
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.warning("channel typing callback raised", exc_info=exc)

    def bind_typing(self, on_start: Callable[[], Awaitable[None]]) -> None:
        self._on_start = on_start

    async def aclose(self) -> None:
        """等待 typing 回调退出，防止回合取消后它继续使用已关闭的适配器。"""
        task = self._typing_task
        self._typing_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def send_json(self, data: dict) -> None:
        frame_type = data.get("type")
        if frame_type == "message.start" and self._on_start is not None:
            self._typing_task = asyncio.create_task(self._on_start())
            self._typing_task.add_done_callback(self._clear_typing_task)
        elif frame_type == "message.complete":
            self.reply_text = data.get("text")
            new_media = data.get("media")
            if isinstance(new_media, list):
                self.media.extend(m for m in new_media if isinstance(m, dict))
        elif frame_type == "error":
            self.error = data.get("message")


async def _run_turn(adapter: ChannelAdapter, state: _ChannelState, batch: list[_QueuedMessage]) -> None:
    """执行一轮 im 回合并投递回复；结束后接管排队消息（整批合并为下一轮前导）或释放单飞行锁。"""
    snapshot = adapter.snapshot
    try:
        reply = await _execute_im_turn(adapter, batch)
        for item in batch:
            if not item.future.done():
                item.future.set_result(reply)
    except asyncio.CancelledError:
        # 用户主动中止：确认已由 handle_inbound 发出，这里只负责让 batch 的 future 落地，
        # 再按协作取消惯例重新抛出。（桌面掉线不再走这条路——那条已在 ipc.discard_user 以错误 resolve。）
        for item in batch:
            if not item.future.done():
                item.future.set_result(None)
        raise
    except Exception:
        logger.exception("channel turn failed", extra={"binding": snapshot.id, "channel": snapshot.channel})
        for item in batch:
            if not item.future.done():
                item.future.set_result(None)
    finally:
        # shield：本函数常在被取消的路径上收尾，而 Lock.acquire 是取消点——不屏蔽的话
        # CancelledError 会在这里二次抛出、跳过整个清理，绑定永久停在「有回合在跑」的假象里。
        task = asyncio.current_task()
        if task is not None:
            await asyncio.shield(_finish_turn(adapter, state, task))


async def _finish_turn(adapter: ChannelAdapter, state: _ChannelState, task: asyncio.Task) -> None:
    """仅由当前 task 接管排队消息或释放单飞行占用。"""
    async with _STATE_LOCK:
        if state.task is not task:
            return
        state.task = None
        state.owner_peer_id = None
        state.cancelling = False
        if state.queue:
            next_batch = list(state.queue)
            state.queue.clear()
            state.owner_peer_id = next_batch[0].msg.peer_id
            state.task = asyncio.create_task(
                _run_turn(adapter, state, next_batch),
                name=f"channels.turn.{adapter.snapshot.id}",
            )


async def _execute_im_turn(adapter: ChannelAdapter, batch: list[_QueuedMessage]) -> str | None:
    """跑一轮完整 chat turn（自带 emitter，不依赖用户 WS——桌面离线也能回），把回复格式化后经渠道送出。

    IM 使用原渠道 emitter，不要求桌面在线；本机工具仍由桌面派发器兑现。批内消息已在接收阶段
    落库（queued 行），回合开始时整批收编消费并清除标记，ChatRequest 只携带末条的行 id。
    """
    snapshot = adapter.snapshot
    last_item = batch[-1]
    last = last_item.msg
    base = SETTINGS.public_base_url.strip().rstrip("/")
    attachments = []
    for attachment in last.attachments:
        url = attachment.url
        if url.startswith("/api/media/files/") and base:
            url = f"{base}{url}"
        attachments.append(
            {"type": attachment.type, "file_url": url, **({"name": attachment.name} if attachment.name else {})},
        )
    async with session_scope() as db:
        binding = await db.get(ChannelBinding, snapshot.id)
        if binding is None:
            return None

        conv = await get_or_create_channel_conversation(
            db,
            binding,
            title_override=_resolve_channel(binding.channel).conversation_title,
        )
        llm_config = await resolve_user_llm_config(db, snapshot.user_id)
        user_settings = await load_user_settings(db, snapshot.user_id)
        # 只收编本批及更早的遗留输入；新到达的下一批保持 queued，不能提前进入当前上下文。
        latest_id = (
            await db.execute(select(func.max(Message.id)).where(Message.conversation_id == conv.id))
        ).scalar() or 0
        await db.execute(
            update(Message)
            .where(Message.conversation_id == conv.id, Message.queued.is_(True), Message.id <= last_item.message_id)
            .values(queued=False, context_order=latest_id + 1),
        )
        await db.commit()

        session_id = str(conv.id)

    emitter = ChannelTurnEmitter()
    if adapter.supports_typing:

        async def _typing_on() -> None:
            try:
                await adapter.send_typing(last.peer_id, last.context_token, status=1)
            except Exception:
                logger.debug("typing indicator failed", extra={"binding": snapshot.id})

        emitter.bind_typing(_typing_on)

    # 两个条件都要满足：WS 在线但 tools.sync 未完成（刚连上）或 Runner 崩溃时，注册表里没有 runner schema，
    # 只看 is_available 会让提示词声称「工具可用」而上下文里根本没有对应工具，诱发幻觉。
    desktop_ready = MANAGER.is_available(snapshot.user_id) and REGISTRY.has_runner_tools(snapshot.user_id)
    environment_hints = resolve_prompt_text(
        _DESKTOP_ONLINE_TEXTS if desktop_ready else _DESKTOP_OFFLINE_TEXTS,
        user_settings.get("language"),
    )
    client_context = ChatRequestClientContext(
        platform_hints=adapter.platform_hint() or None,
        environment_hints=environment_hints,
    )
    req = ChatRequest(
        session_id=session_id,
        message=ChatMessageRequest(
            role="user",
            content=last.text,
            attachments=attachments or None,
        ),
        client_context=client_context,
    )
    try:
        await run_chat_turn(req, llm_config, snapshot.user_id, emitter, persisted_message_id=last_item.message_id)
    except Exception:
        logger.exception("im chat turn crashed", extra={"binding": snapshot.id, "channel": snapshot.channel})
        return None
    finally:
        await emitter.aclose()

    if emitter.error:
        logger.warning("im turn ended with error frame", extra={"binding": snapshot.id, "error": emitter.error})
        return None
    if not (emitter.reply_text or "").strip():
        return None

    plain = strip_markdown(emitter.reply_text)
    payload = ChannelDeliveryPayload.model_validate({"text": plain, "media": emitter.media})
    async with _state_for(snapshot.id).delivery_lock:
        remaining, delivered = await _deliver_reply(adapter, last, payload)
        if remaining.text or remaining.media:
            await _enqueue_delivery(snapshot.id, last.peer_id, remaining)
    if not delivered:
        try:
            await adapter.send_text(last.peer_id, _DELIVERY_FAILED_FALLBACK, last.context_token)
        except Exception:
            logger.error("fallback delivery also failed", extra={"binding": snapshot.id})
    return plain


async def _deliver_reply(
    adapter: ChannelAdapter,
    msg: InboundMessage,
    payload: ChannelDeliveryPayload,
) -> tuple[ChannelDeliveryPayload, bool]:
    """返回剩余内容与是否送达任何片段；成功部分不参与补发。"""
    chunks = chunk_text(payload.text, SETTINGS.weixin_reply_max_chars)
    remaining = ChannelDeliveryPayload(media=payload.media)
    delivered = False
    if payload.media:
        try:
            await adapter.send_media(
                msg.peer_id,
                chunks[0] if chunks else None,
                [m.model_dump() for m in payload.media],
                msg.context_token,
            )
        except Exception:
            logger.exception("media reply delivery failed", extra={"binding": adapter.snapshot.id})
        else:
            delivered = True
            remaining.media = []
            chunks = chunks[1:]
    failed_chunks: list[str] = []
    for chunk in chunks:
        try:
            await adapter.send_text(msg.peer_id, chunk, msg.context_token)
        except Exception:
            logger.exception("reply chunk delivery failed", extra={"binding": adapter.snapshot.id})
            failed_chunks.append(chunk)
        else:
            delivered = True
    remaining.text = "\n".join(failed_chunks)
    return remaining, delivered


async def _enqueue_delivery(
    binding_id: int,
    peer_id: str | None,
    payload: ChannelDeliveryPayload,
) -> None:
    """把未送达的回复/任务结果持久化为待补发行；peer_id 为空表示发起对端未知（后台任务产物）。"""
    try:
        async with session_scope() as db:
            db.add(
                ChannelDelivery(
                    binding_id=binding_id,
                    peer_id=peer_id or "",
                    payload_json=payload.model_dump_json(),
                ),
            )
            await db.commit()
    except Exception:
        logger.warning("failed to persist channel delivery", extra={"binding": binding_id}, exc_info=True)


def _schedule_delivery_flush(
    adapter: ChannelAdapter,
    state: _ChannelState,
    peer_id: str,
    context_token: str | None,
) -> None:
    """对端来消息即拿到新鲜回复上下文：后台补发此前未能送达的结果（不重新执行任何任务）。"""
    adapter.create_task(
        _flush_pending_deliveries(adapter, state, peer_id, context_token),
        name=f"channels.deliver.{adapter.snapshot.id}",
    )


async def _flush_pending_deliveries(
    adapter: ChannelAdapter,
    state: _ChannelState,
    peer_id: str,
    context_token: str | None,
) -> None:
    """逐条补发该对端（及对端未定）的待交付行；一次失败即停止本轮，等下一次入站再试。"""
    binding_id = adapter.snapshot.id
    msg = InboundMessage(
        channel=adapter.channel_name,
        peer_id=peer_id,
        peer_name="",
        text="",
        context_token=context_token,
    )
    async with state.delivery_lock:
        while True:
            async with session_scope() as db:
                row = (
                    await db.execute(
                        select(ChannelDelivery)
                        .where(
                            ChannelDelivery.binding_id == binding_id,
                            ChannelDelivery.status == "pending",
                            ChannelDelivery.peer_id.in_((peer_id, "")),
                        )
                        .order_by(ChannelDelivery.id.asc())
                        .limit(1),
                    )
                ).scalar_one_or_none()
                if row is None:
                    return
                try:
                    payload = ChannelDeliveryPayload.model_validate_json(row.payload_json)
                except ValidationError:
                    row.status = "abandoned"
                    await db.commit()
                    logger.error("invalid channel delivery payload", extra={"delivery_id": row.id})
                    continue
                row_id = row.id
            remaining, _ = await _deliver_reply(adapter, msg, payload)
            async with session_scope() as db:
                fresh = await db.get(ChannelDelivery, row_id)
                if fresh is None or fresh.status != "pending":
                    continue
                if remaining.text or remaining.media:
                    fresh.payload_json = remaining.model_dump_json()
                    fresh.attempts += 1
                    if fresh.attempts >= SETTINGS.channels_delivery_max_attempts:
                        fresh.status = "abandoned"
                        logger.error(
                            "channel delivery abandoned after repeated failures",
                            extra={"binding": binding_id, "delivery_id": row_id},
                        )
                else:
                    fresh.status = "sent"
                    fresh.sent_at = datetime.now(UTC)
                await db.commit()
            if remaining.text or remaining.media:
                return
