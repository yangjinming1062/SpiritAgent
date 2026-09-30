import asyncio
import hashlib
import json
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from components import SETTINGS, get_logger, resolve_prompt_text, session_scope
from modules.auth import ChatRequestClientContext
from modules.channels import ChannelBinding, ChannelDelivery, ChannelDeliveryPayload, ChannelPeer
from modules.conversation import Message
from modules.settings import get_user_setting
from modules.system import ChatMessageRequest, ChatRequest
from modules.ws import COMPANION_TURN_EVENT, emit_ws_event
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.chat import persist_queued_inbound_message, run_chat_turn
from services.domains.companion import note_user_contact
from services.infrastructure.desktop import MANAGER
from services.infrastructure.event_store import interrupt_user_event_tasks
from services.infrastructure.llm import resolve_user_llm_config
from services.infrastructure.tool_runtime import REGISTRY

from .base import ChannelAdapter, ChannelBindingSnapshot, InboundMessage
from .conversation import get_or_create_channel_conversation_id
from .formatting import chunk_text, strip_markdown

logger = get_logger(__name__)

# 未知对端的首条消息触发的固定配对回复（pending 态只发一次）；放行由主人在 Hub / REST 审批。
PAIRING_NOTICE = "（对方尚未通过配对审批，已请主人确认；批准后再继续对话）"

# 回合已产出但渠道投递全失败时的兜底提示：不静默，让对端知道伙伴方才说话了。
_DELIVERY_FAILED_FALLBACK = "（伙伴刚想说话，但消息没能送达到这个渠道，请稍后再试）"

# 队列容量不足时的明确拒收提示：消息不入队也不落库，对端知道这条没被接收、可重发。
_QUEUE_FULL_NOTICE = "（正在处理的任务比较多，这条消息没有被接收；请稍后再发一次）"

# 必须全等比较而非包含匹配——「请不要停下来」「去停车场」都含「停」，模糊匹配会把正常发言吞成中断。
_STOP_COMMANDS = frozenset({"停", "停止", "停下", "stop", "/stop"})

_STOP_ACK = "（已停止本轮回复）"

# 桌面连接状态注入 IM 回合的环境事实；离线时提示只报告可验证的「当前未连接」，不猜测关机/断网等原因。
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
    conversation_id: int


@dataclass
class _ChannelState:
    """每绑定一个桥接状态：单飞行由 task 句柄单独表达（不另设 in_flight 布尔，避免同一事实两份副本漏改导致卡住或中止落空）+ 排队队列。"""

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


async def stop_binding_turns(binding_id: int) -> None:
    """停止并等待一个绑定的当前回合并清空排队：只取消不清队列会让收尾立刻起下一批；被清出的消息已落库为 queued 行，下一轮收编消费。"""
    async with _STATE_LOCK:
        state = _STATES.pop(binding_id, None)
        if state is None:
            return
        state.queue.clear()
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


async def _is_duplicate(db: AsyncSession, dedup_key: str) -> bool:
    return (await db.execute(select(Message.id).where(Message.dedup_key == dedup_key))).scalar_one_or_none() is not None


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


async def handle_inbound(adapter: ChannelAdapter, msg: InboundMessage) -> None:
    """入站闸门：白名单 → 主动联动 → 中止判定 → 频控 → 接收落库入队；回合在独立任务执行。落库完成后适配器即可推进渠道游标，回复由回合任务投递。"""
    snapshot = adapter.snapshot
    dedup_key = _dedup_key(snapshot, msg)
    async with session_scope() as db:
        if await db.get(ChannelBinding, snapshot.id) is None:
            return
        peer = await _get_peer(db, snapshot.id, msg.peer_id)
        first_pending = peer is None
        if peer is None:
            peer = ChannelPeer(binding_id=snapshot.id, peer_id=msg.peer_id, peer_name=msg.peer_name, status="pending")
            db.add(peer)
        else:
            peer.peer_name = msg.peer_name or peer.peer_name
        peer.last_message_at = datetime.now(UTC)
        try:
            await db.commit()
        except IntegrityError:
            # 并发首条同 peer：判负方回滚后重读胜者行，按既有对端继续走闸门（配对回复只由胜者发一次）。
            await db.rollback()
            peer = await _get_peer(db, snapshot.id, msg.peer_id)
            if peer is None:
                return
            first_pending = False
        peer_status = peer.status
        if peer_status == "allowed" and dedup_key is not None and await _is_duplicate(db, dedup_key):
            return

    if peer_status != "allowed":
        if peer_status == "pending" and first_pending:
            # 默认拒绝 + 一次性配对提示：既不让陌生人消耗 LLM 回合，也不完全冷拒。
            try:
                await adapter.send_text(msg.peer_id, PAIRING_NOTICE, msg.context_token)
            except Exception:
                logger.exception("pairing notice delivery failed", extra={"binding": snapshot.id, "peer": msg.peer_id})
            await _emit_peer_request(snapshot.user_id, snapshot.channel, msg)
        return

    # IM 用户消息同样优先中断主动回合、刷新接触计时——与 prompt.submit 同一契约。
    note_user_contact(snapshot.user_id)
    await interrupt_user_event_tasks(snapshot.user_id, COMPANION_TURN_EVENT)

    state = _state_for(snapshot.id)

    # 中止判定必须在频控之前：用户眼看伙伴做错事时往往连发几条打满窗口，先频控会把唯一的刹车一起丢掉；中止不消耗窗口配额。
    if msg.text.strip().lower() in _STOP_COMMANDS:
        stopped = False
        consumed = False
        async with _STATE_LOCK:
            # 只有发起该回合的对端能中止（多对端绑定下别人无权叫停）；空闲态「停」当普通话照常走回合。
            if (task := state.task) is not None and state.owner_peer_id == msg.peer_id:
                consumed = True
                if not state.cancelling:
                    # 清队列防止被取消回合的收尾立刻起下一批；清出的消息已落库，留待后续回合收编。
                    state.queue.clear()
                    # 句柄保留到 task 自己完成清理；旧 task 无权清除期间可能接管的新 task。
                    state.cancelling = True
                    task.cancel()
                    stopped = True
        # 确认在锁外发：cancel 只是排程，被取消回合的 finally 还要抢同一把锁，在锁内 await 投递会一直挡住它。
        if stopped:
            try:
                await adapter.send_text(msg.peer_id, _STOP_ACK, msg.context_token)
            except Exception:
                logger.exception("stop ack delivery failed", extra={"binding": snapshot.id})
            return
        if consumed:
            return

    if _rate_exceeded(state, msg.peer_id):
        logger.warning("inbound rate limit exceeded, dropping", extra={"binding": snapshot.id, "peer": msg.peer_id})
        return

    # 重投消息没有新鲜回复上下文，不消耗补发尝试次数。
    if await _intake_message(adapter, state, msg, dedup_key):
        adapter.create_task(
            _flush_pending_deliveries(adapter, state, msg.peer_id, msg.context_token),
            name=f"channels.deliver.{snapshot.id}",
        )


async def _intake_message(
    adapter: ChannelAdapter,
    state: _ChannelState,
    msg: InboundMessage,
    dedup_key: str | None,
) -> bool:
    """接收段：容量校验 → 先持久化（queued 行 + 去重）→ 单飞行入队；intake 锁保证落库/入队序等于投递序。容量不足在落库前明确拒收（已落库的不静默丢弃，被拒收的不落库可重发）；返回是否为新接收或明确拒收。"""
    snapshot = adapter.snapshot
    async with state.intake_lock:
        async with _STATE_LOCK:
            busy = state.task is not None and len(state.queue) >= SETTINGS.channels_turn_queue_max
        if busy:
            if dedup_key is not None:
                async with session_scope() as db:
                    if await _is_duplicate(db, dedup_key):
                        return False
            logger.warning("turn queue full; rejecting inbound", extra={"binding": snapshot.id, "peer": msg.peer_id})
            try:
                await adapter.send_text(msg.peer_id, _QUEUE_FULL_NOTICE, msg.context_token)
            except Exception:
                logger.exception("queue-full notice delivery failed", extra={"binding": snapshot.id})
            return True

        base = SETTINGS.public_base_url.strip().rstrip("/")
        attachments = [
            {
                "type": a.type,
                "file_url": f"{base}{a.url}" if base and a.url.startswith("/api/media/files/") else a.url,
            }
            for a in msg.attachments
        ]
        async with session_scope() as db:
            binding = await db.get(ChannelBinding, snapshot.id)
            if binding is None:
                return False
            conversation_id = await get_or_create_channel_conversation_id(db, binding, adapter.conversation_title)
            row = await persist_queued_inbound_message(
                db,
                conversation_id,
                text=msg.text,
                attachments=attachments,
                dedup_key=dedup_key,
            )
            if row is None:
                # 渠道重投的重复消息：同标识行已存在，不再入队或回复。
                logger.info("duplicate inbound dropped", extra={"binding": snapshot.id, "peer": msg.peer_id})
                return False

        item = _QueuedMessage(msg=msg, message_id=row.id, conversation_id=conversation_id)
        async with _STATE_LOCK:
            if _STATES.get(snapshot.id) is not state:
                return False
            if state.task is not None:
                state.queue.append(item)
            else:
                state.owner_peer_id = msg.peer_id
                state.task = asyncio.create_task(_run_turn(adapter, state, [item]), name=f"channels.turn.{snapshot.id}")
    return True


class ChannelTurnEmitter:
    """无头回合发射器：捕获终端回复并转发 typing，工具中间轮不送出正文。"""

    def __init__(self, on_start: Callable[[], Coroutine[Any, Any, None]] | None) -> None:
        self.reply_text: str | None = None
        self.error: str | None = None
        self.media: list[dict] = []
        self._on_start = on_start
        self._typing_task: asyncio.Task | None = None

    def _clear_typing_task(self, task: asyncio.Task) -> None:
        # 仅当仍是当前 task 时清空：新一轮 typing 已覆盖时，旧回调安全跳过。
        if self._typing_task is task:
            self._typing_task = None
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.warning("channel typing callback raised", exc_info=exc)

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
    try:
        await _execute_im_turn(adapter, state, batch)
    except Exception:
        snapshot = adapter.snapshot
        logger.exception("channel turn failed", extra={"binding": snapshot.id, "channel": snapshot.channel})
    finally:
        # shield：收尾常在被取消路径上，Lock.acquire 是取消点，不屏蔽会让 CancelledError 二次抛出并跳过清理，绑定永久假占用。
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


async def _execute_im_turn(adapter: ChannelAdapter, state: _ChannelState, batch: list[_QueuedMessage]) -> None:
    """跑一轮 chat turn（自带渠道 emitter，桌面离线也能回），回复格式化后经渠道送出。批内消息已在接收阶段落库，回合开始整批收编并清 queued 标记；ChatRequest 只携带末条行 id。"""
    snapshot = adapter.snapshot
    last_item = batch[-1]
    last = last_item.msg
    conversation_id = last_item.conversation_id
    async with session_scope() as db:
        llm_config = await resolve_user_llm_config(db, snapshot.user_id)
        language = await get_user_setting(db, snapshot.user_id, "language")
        # 只收编本批及更早的遗留输入；新到达的下一批保持 queued，不能提前进入当前上下文。
        latest_id = (
            await db.execute(select(func.max(Message.id)).where(Message.conversation_id == conversation_id))
        ).scalar() or 0
        await db.execute(
            update(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.queued.is_(True),
                Message.id <= last_item.message_id,
            )
            .values(queued=False, context_order=latest_id + 1),
        )
        await db.commit()

    emitter = ChannelTurnEmitter(
        partial(adapter.send_typing, last.peer_id, last.context_token) if adapter.supports_typing else None,
    )
    # 两个条件都要满足：仅 is_available 时若 tools.sync 未完成或 Runner 崩溃，提示词会声称工具可用但上下文无对应工具，诱发幻觉。
    desktop_ready = MANAGER.is_available(snapshot.user_id) and REGISTRY.has_runner_tools(snapshot.user_id)
    req = ChatRequest(
        session_id=str(conversation_id),
        message=ChatMessageRequest(content=last.text),
        client_context=ChatRequestClientContext(
            platform_hints=adapter.platform_hint(),
            environment_hints=resolve_prompt_text(
                _DESKTOP_ONLINE_TEXTS if desktop_ready else _DESKTOP_OFFLINE_TEXTS,
                language,
            ),
        ),
    )
    try:
        await run_chat_turn(
            req,
            llm_config,
            snapshot.user_id,
            emitter,
            persisted_message_id=last_item.message_id,
            headless=True,
        )
    except Exception:
        logger.exception("im chat turn crashed", extra={"binding": snapshot.id, "channel": snapshot.channel})
        return
    finally:
        await emitter.aclose()

    if emitter.error:
        logger.warning("im turn ended with error frame", extra={"binding": snapshot.id, "error": emitter.error})
        return
    if not emitter.reply_text or not emitter.reply_text.strip():
        return

    payload = ChannelDeliveryPayload.model_validate(
        {"text": strip_markdown(emitter.reply_text), "media": emitter.media},
    )
    if not payload.text and not payload.media:
        return
    async with state.delivery_lock:
        remaining, delivered = await _deliver_reply(adapter, last.peer_id, last.context_token, payload)
        if remaining.text or remaining.media:
            await _enqueue_delivery(snapshot.id, last.peer_id, remaining)
    if not delivered:
        try:
            await adapter.send_text(last.peer_id, _DELIVERY_FAILED_FALLBACK, last.context_token)
        except Exception:
            logger.error("fallback delivery also failed", extra={"binding": snapshot.id})


async def _deliver_reply(
    adapter: ChannelAdapter,
    peer_id: str,
    context_token: str | None,
    payload: ChannelDeliveryPayload,
) -> tuple[ChannelDeliveryPayload, bool]:
    """返回剩余内容与是否送达任何片段；成功部分不参与补发。"""
    chunks = chunk_text(payload.text, SETTINGS.weixin_reply_max_chars) if payload.text else []
    remaining = ChannelDeliveryPayload(media=payload.media)
    delivered = False
    if payload.media:
        try:
            await adapter.send_media(peer_id, chunks[0] if chunks else None, payload.media, context_token)
        except Exception:
            logger.exception("media reply delivery failed", extra={"binding": adapter.snapshot.id})
        else:
            delivered = True
            remaining.media = []
            chunks = chunks[1:]
    failed_chunks: list[str] = []
    for chunk in chunks:
        try:
            await adapter.send_text(peer_id, chunk, context_token)
        except Exception:
            logger.exception("reply chunk delivery failed", extra={"binding": adapter.snapshot.id})
            failed_chunks.append(chunk)
        else:
            delivered = True
    remaining.text = "\n".join(failed_chunks)
    return remaining, delivered


async def _enqueue_delivery(binding_id: int, peer_id: str, payload: ChannelDeliveryPayload) -> None:
    """把未送达的回复持久化为待补发行；后台任务产物以空 peer_id 另行写入（对端未定）。"""
    try:
        async with session_scope() as db:
            db.add(ChannelDelivery(binding_id=binding_id, peer_id=peer_id, payload_json=payload.model_dump_json()))
            await db.commit()
    except Exception:
        logger.warning("failed to persist channel delivery", extra={"binding": binding_id}, exc_info=True)


async def _flush_pending_deliveries(
    adapter: ChannelAdapter,
    state: _ChannelState,
    peer_id: str,
    context_token: str | None,
) -> None:
    """对端来消息即拿到新鲜回复上下文：逐条补发该对端（及对端未定）的待交付行，不重新执行任务；一次失败即停，等下次入站再试。"""
    binding_id = adapter.snapshot.id
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
            remaining, _ = await _deliver_reply(adapter, peer_id, context_token, payload)
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
