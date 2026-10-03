import asyncio
import hashlib
import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from components import SETTINGS, get_logger, resolve_prompt_text, session_scope
from modules.auth import ChatRequestClientContext
from modules.channels import ChannelBinding, ChannelDelivery, ChannelDeliveryPayload, ChannelPeer, ChannelTurnSource
from modules.conversation import Message
from modules.settings import get_user_setting
from modules.system import ChatAttachment, ChatMessageRequest, ChatRequest
from modules.ws import COMPANION_TURN_EVENT, emit_ws_event
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.chat import persist_queued_inbound_message, run_chat_turn
from services.domains.companion import note_user_contact
from services.infrastructure.assets import normalize_asset_reference
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

_TURN_FAILED_TEXTS = {
    "zh": "（系统提示：本次处理未完成。已开始的操作可能已经生效，请先核对结果再重试。）",
    "en": "(System notice: this request did not complete. Operations already started may have taken effect; check their results before trying again.)",
}
_OWNER_FAILURE_TITLE_TEXTS = {"zh": "聊天通道处理失败", "en": "Chat channel request failed"}
_OWNER_FAILURE_TEXTS = {
    "zh": "聊天通道的一次请求未完成。请查看会话；已开始的电脑操作可能已经生效，请先核对结果。",
    "en": "A chat channel request did not complete. Review the conversation and check any computer operations already started before retrying.",
}

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
    authorization_epoch: int


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
    peer_epochs: dict[str, int] = field(default_factory=dict)


_STATES: dict[int, _ChannelState] = {}


def _state_for(binding_id: int) -> _ChannelState:
    return _STATES.setdefault(binding_id, _ChannelState())


@asynccontextmanager
async def peer_access_update(binding_id: int) -> AsyncIterator[None]:
    """对端权限更新与入站持久化共用接收锁，防止撤权清理后迟到输入再次入队。"""
    async with _state_for(binding_id).intake_lock:
        yield


async def revoke_peer_messages(db: AsyncSession, binding_id: int, peer_id: str) -> None:
    """在接收锁内原子提交撤权与丢弃状态，再停稳该对端回合；已执行历史保留。"""
    await db.flush()
    conversation_id = await db.scalar(select(ChannelBinding.conversation_id).where(ChannelBinding.id == binding_id))
    if conversation_id is not None:
        await db.execute(
            update(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.channel_peer_id == peer_id,
                Message.queued.is_(True),
            )
            .values(queued=False, discarded=True),
        )
    await db.execute(
        update(ChannelDelivery)
        .where(
            ChannelDelivery.binding_id == binding_id,
            ChannelDelivery.peer_id == peer_id,
            ChannelDelivery.status == "pending",
        )
        .values(status="abandoned"),
    )
    await db.commit()
    state = _state_for(binding_id)
    async with _STATE_LOCK:
        state.peer_epochs[peer_id] = state.peer_epochs.get(peer_id, 0) + 1
        state.queue = deque(item for item in state.queue if item.msg.peer_id != peer_id)
        task = state.task if state.owner_peer_id == peer_id else None
        if task is not None and not task.done():
            state.cancelling = True
            task.cancel()
    if task is not None and task is not asyncio.current_task():
        await asyncio.gather(task, return_exceptions=True)
    # 已发出的请求不能撤回；等待最后一次投递结束，下一块和补发均重新检查权限。
    async with state.delivery_lock:
        pass


async def _peer_allowed(binding_id: int, peer_id: str, user_id: int) -> bool:
    async with session_scope() as db:
        allowed = await db.scalar(
            select(ChannelPeer.id)
            .join(ChannelBinding, ChannelBinding.id == ChannelPeer.binding_id)
            .where(
                ChannelPeer.binding_id == binding_id,
                ChannelPeer.peer_id == peer_id,
                ChannelPeer.status == "allowed",
                ChannelBinding.user_id == user_id,
            ),
        )
    return allowed is not None


async def _turn_authorized(
    adapter: ChannelAdapter,
    state: _ChannelState,
    peer_id: str,
    authorization_epoch: int,
) -> bool:
    if _STATES.get(adapter.snapshot.id) is not state or state.peer_epochs.get(peer_id, 0) != authorization_epoch:
        return False
    return await _peer_allowed(adapter.snapshot.id, peer_id, adapter.snapshot.user_id)


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
    """接收段：容量校验 → 下载渠道媒体 → 先持久化（queued 行 + 去重）→ 单飞行入队；intake 锁保证落库/入队序等于投递序。容量不足在落库前明确拒收（已落库的不静默丢弃，被拒收的不落库可重发）；返回是否为新接收或明确拒收。"""
    snapshot = adapter.snapshot
    async with state.intake_lock:
        if not await _peer_allowed(snapshot.id, msg.peer_id, snapshot.user_id):
            return False
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

        fetched = await msg.fetch_attachments() if msg.fetch_attachments is not None else ()
        attachments = [ChatAttachment(type=a.type, file_url=a.url) for a in (*msg.attachments, *fetched)]
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
                channel_peer_id=msg.peer_id,
            )
            if row is None:
                # 渠道重投的重复消息：同标识行已存在，不再入队或回复。
                logger.info("duplicate inbound dropped", extra={"binding": snapshot.id, "peer": msg.peer_id})
                return False

        item = _QueuedMessage(
            msg=msg,
            message_id=row.id,
            conversation_id=conversation_id,
            authorization_epoch=state.peer_epochs.get(msg.peer_id, 0),
        )
        async with _STATE_LOCK:
            if _STATES.get(snapshot.id) is not state:
                return False
            if state.task is not None:
                state.queue.append(item)
            else:
                state.owner_peer_id = msg.peer_id
                state.task = adapter.create_task(_run_turn(adapter, state, [item]), name=f"channels.turn.{snapshot.id}")
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
            self.error = data.get("detail") or data.get("message")


async def _run_turn(adapter: ChannelAdapter, state: _ChannelState, batch: list[_QueuedMessage]) -> None:
    """执行一轮 im 回合并投递回复；结束后接管同一对端连续排队消息或释放单飞行锁。"""
    try:
        await _execute_im_turn(adapter, state, batch)
    except Exception:
        snapshot = adapter.snapshot
        logger.exception("channel turn failed", extra={"binding": snapshot.id, "channel": snapshot.channel})
        await _report_turn_failure(adapter, state, batch[-1])
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
            first = state.queue.popleft()
            next_batch = [first]
            state.owner_peer_id = first.msg.peer_id
            while state.queue and state.queue[0].msg.peer_id == state.owner_peer_id:
                next_batch.append(state.queue.popleft())
            state.task = adapter.create_task(
                _run_turn(adapter, state, next_batch),
                name=f"channels.turn.{adapter.snapshot.id}",
            )


async def _execute_im_turn(adapter: ChannelAdapter, state: _ChannelState, batch: list[_QueuedMessage]) -> None:
    """跑一轮 chat turn（自带渠道 emitter，桌面离线也能回），回复格式化后经渠道送出。批内消息已在接收阶段落库，回合开始整批收编并清 queued 标记；ChatRequest 只携带末条行 id。"""
    snapshot = adapter.snapshot
    last_item = batch[-1]
    last = last_item.msg
    conversation_id = last_item.conversation_id
    authorization_check = partial(_turn_authorized, adapter, state, last.peer_id, last_item.authorization_epoch)
    if not await authorization_check():
        return
    async with session_scope() as db:
        llm_config = await resolve_user_llm_config(db, snapshot.user_id)
        peer = await _get_peer(db, snapshot.id, last.peer_id)
        if peer is None or peer.status != "allowed":
            return
        channel_source = ChannelTurnSource(
            binding_id=snapshot.id,
            peer_record_id=peer.id,
            peer_id=peer.peer_id,
            authorization_revision=peer.authorization_revision,
        )
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
                Message.discarded.is_(False),
                Message.channel_peer_id == last.peer_id,
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
            authorization_check=authorization_check,
            channel_source=channel_source,
        )
    except Exception:
        logger.exception("im chat turn crashed", extra={"binding": snapshot.id, "channel": snapshot.channel})
        await _report_turn_failure(adapter, state, last_item)
        return
    finally:
        await emitter.aclose()

    if emitter.error:
        logger.warning("im turn ended with error frame", extra={"binding": snapshot.id, "error": emitter.error})
        await _report_turn_failure(adapter, state, last_item)
        return
    payload = ChannelDeliveryPayload.model_validate(
        {"text": strip_markdown(emitter.reply_text or ""), "media": emitter.media},
    )
    if not payload.text and not payload.media:
        return
    async with state.delivery_lock:
        if not await authorization_check():
            return
        remaining, delivered = await _deliver_reply(adapter, last.peer_id, last.context_token, payload)
        if (remaining.text or remaining.media) and await authorization_check():
            await _enqueue_delivery(snapshot.id, last.peer_id, remaining)
    if not delivered:
        try:
            if await authorization_check():
                await adapter.send_text(last.peer_id, _DELIVERY_FAILED_FALLBACK, last.context_token)
        except Exception:
            logger.error("fallback delivery also failed", extra={"binding": snapshot.id})


async def _report_turn_failure(adapter: ChannelAdapter, state: _ChannelState, item: _QueuedMessage) -> None:
    """生成错误使用系统提示，不泄露供应商原因；通知与补发不重新执行回合。撤权与用户取消不报失败。"""
    snapshot = adapter.snapshot
    try:
        if not await _turn_authorized(adapter, state, item.msg.peer_id, item.authorization_epoch):
            return
        async with session_scope() as db:
            language = await get_user_setting(db, snapshot.user_id, "language")
            emit_ws_event(
                db,
                user_id=snapshot.user_id,
                event_type="system.notification",
                payload={
                    "kind": "error",
                    "title": resolve_prompt_text(_OWNER_FAILURE_TITLE_TEXTS, language),
                    "message": resolve_prompt_text(_OWNER_FAILURE_TEXTS, language),
                    "session_id": str(item.conversation_id),
                },
            )
            await db.commit()
        async with state.delivery_lock:
            if not await _turn_authorized(adapter, state, item.msg.peer_id, item.authorization_epoch):
                return
            payload = ChannelDeliveryPayload(text=resolve_prompt_text(_TURN_FAILED_TEXTS, language))
            remaining, _ = await _deliver_reply(adapter, item.msg.peer_id, item.msg.context_token, payload)
            if remaining.text and await _turn_authorized(adapter, state, item.msg.peer_id, item.authorization_epoch):
                await _enqueue_delivery(snapshot.id, item.msg.peer_id, remaining)
    except Exception:
        logger.exception("channel failure notification failed", extra={"binding": snapshot.id})


async def _delivery_allowed(adapter: ChannelAdapter, peer_id: str, source: ChannelTurnSource | None) -> bool:
    if source is None:
        return await _peer_allowed(adapter.snapshot.id, peer_id, adapter.snapshot.user_id)
    if source.binding_id != adapter.snapshot.id or source.peer_id != peer_id:
        return False
    async with session_scope() as db:
        return await ChannelPeer.authorizes(db, adapter.snapshot.user_id, source)


async def _deliver_reply(
    adapter: ChannelAdapter,
    peer_id: str,
    context_token: str | None,
    payload: ChannelDeliveryPayload,
) -> tuple[ChannelDeliveryPayload, bool]:
    """返回剩余内容与是否送达任何片段；成功部分不参与补发。"""
    chunks = chunk_text(payload.text, SETTINGS.weixin_reply_max_chars) if payload.text else []
    remaining = ChannelDeliveryPayload(media=payload.media, channel_source=payload.channel_source)
    delivered = False
    if payload.media:
        if not await _delivery_allowed(adapter, peer_id, payload.channel_source):
            return ChannelDeliveryPayload(), False
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
        if not await _delivery_allowed(adapter, peer_id, payload.channel_source):
            return ChannelDeliveryPayload(), delivered
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
        stored = payload.model_copy(deep=True)
        for media in stored.media:
            media.url = normalize_asset_reference(media.url) or media.url
        async with session_scope() as db:
            if peer_id:
                allowed = await db.scalar(
                    select(ChannelPeer.id)
                    .where(
                        ChannelPeer.binding_id == binding_id,
                        ChannelPeer.peer_id == peer_id,
                        ChannelPeer.status == "allowed",
                    )
                    .with_for_update(),
                )
                if allowed is None:
                    return
            if stored.channel_source is not None:
                binding = await db.get(ChannelBinding, binding_id)
                if (
                    binding is None
                    or stored.channel_source.binding_id != binding_id
                    or stored.channel_source.peer_id != peer_id
                    or not await ChannelPeer.authorizes(db, binding.user_id, stored.channel_source, lock=True)
                ):
                    return
            db.add(ChannelDelivery(binding_id=binding_id, peer_id=peer_id, payload_json=stored.model_dump_json()))
            await db.commit()
    except Exception:
        logger.warning("failed to persist channel delivery", extra={"binding": binding_id}, exc_info=True)


async def _flush_pending_deliveries(
    adapter: ChannelAdapter,
    state: _ChannelState,
    peer_id: str,
    context_token: str | None,
) -> None:
    """来信后只补发该对端的待交付行；无归属旧记录作废，失败等下次来信重试，不重新执行任务。"""
    binding_id = adapter.snapshot.id
    async with state.delivery_lock:
        while True:
            if not await _peer_allowed(binding_id, peer_id, adapter.snapshot.user_id):
                return
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
                if not row.peer_id or not await _delivery_allowed(adapter, peer_id, payload.channel_source):
                    row.status = "abandoned"
                    await db.commit()
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
