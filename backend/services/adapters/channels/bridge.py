import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from components import SETTINGS, get_logger, resolve_prompt_text, session_scope
from modules.auth import ChatRequestClientContext
from modules.channels import ChannelBinding, ChannelPeer
from modules.system import ChatMessageRequest, ChatRequest
from modules.ws import emit_ws_event
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.chat import load_user_settings, persist_extra_user_messages, run_chat_turn
from services.domains.conversation import note_user_contact, reset_user_outreach
from services.infrastructure.desktop.connection import MANAGER
from services.infrastructure.llm import resolve_user_llm_config
from services.infrastructure.tool_runtime import REGISTRY

from .base import ChannelAdapter, InboundMessage
from .conversation import get_or_create_channel_conversation
from .formatting import chunk_text, strip_markdown
from .registry import resolve as _resolve_channel

logger = get_logger(__name__)

# 未知对端的首条消息触发的固定配对回复（pending 态只发一次）；放行由主人在 Hub / REST 审批。
PAIRING_NOTICE = "我还不认识你哦～已经请伙伴的主人确认了，批准之后我们再聊吧！"

# 回合已产出但渠道投递全失败时的兜底提示：不静默，让对端知道伙伴方才说话了。
_DELIVERY_FAILED_FALLBACK = "（伙伴刚想说话，但消息没能送达到这个渠道，请稍后再试）"

# 中止指令白名单。必须**全等**比较而非包含匹配——「请不要停下来」「去停车场」都含「停」，
# 模糊匹配会把正常发言吞成中断。已审批对端等同本人、可驱动本机终端，中止是唯一的刹车。
_STOP_COMMANDS = frozenset({"停", "停止", "停下", "stop", "/stop"})

_STOP_ACK = "好，我停下了。"

# 桌面在线与否作为环境事实注入 IM 回合的系统提示词。桌面离线时 runner 工具会被整体清出注册表，
# LLM 连工具都看不见，若不明说就只会含糊拒绝——用户要的是「你电脑没开机」这句实话。
_DESKTOP_ONLINE_TEXTS = {
    "zh": "【环境】主人的电脑当前在线，你可以使用本机工具（读写文件、终端、浏览器等）帮他做事。",
    "en": "[Environment] The user's desktop is online; you can use the local tools (files, terminal, browser, etc.) to act on it.",
}

_DESKTOP_OFFLINE_TEXTS = {
    "zh": "【环境】主人的电脑当前不在线，本机工具全部不可用。遇到需要操作电脑的请求，如实告诉他电脑没开机或没连上，等开机后再帮他做，不要假装做过或含糊搪塞。",
    "en": "[Environment] The user's desktop is offline, so no local tools are available. If asked to do something on the computer, say plainly that it is not connected and offer to do it once it is back — never pretend it was done.",
}

# 入站闸门串行化：state 读写与单飞行转移必须原子。
_STATE_LOCK = asyncio.Lock()


@dataclass
class _QueuedMessage:
    msg: InboundMessage
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


async def handle_inbound(adapter: ChannelAdapter, msg: InboundMessage) -> asyncio.Future[str | None]:
    """入站闸门：白名单检查 → 主动状态联动 → 单飞行入队；返回 per-message future（回合完成时以回复 resolve）。

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

    # IM 侧的用户消息同样终结主动外联节奏、刷新接触计时——与 prompt.submit 同一契约。
    reset_user_outreach(snapshot.user_id)
    note_user_contact(snapshot.user_id)

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

    item = _QueuedMessage(msg=msg, future=asyncio.get_running_loop().create_future())
    async with _STATE_LOCK:
        if state.task is not None:
            if len(state.queue) >= SETTINGS.channels_turn_queue_max:
                dropped = state.queue.popleft()
                if not dropped.future.done():
                    dropped.future.set_result(None)
                logger.warning("turn queue full, dropped oldest", extra={"binding": snapshot.id})
            state.queue.append(item)
        else:
            state.owner_peer_id = msg.peer_id
            state.task = asyncio.create_task(_run_turn(adapter, state, [item]), name=f"channels.turn.{snapshot.id}")
    return item.future


class ChannelTurnEmitter:
    """无头回合发射器：捕获终端 ``message.complete`` 文本，typing 与中间进度转发给适配器。

    实现 application/chat 的 Emitter 协议（send_json）；帧全部留存便于调试，不做 WS 翻译——
    桌面端不实时旁观 IM 回合（P3 再议），历史经 im 会话 REST 读取。

    中间进度：编排器只在终局那一轮发 message.complete，带工具调用的中间迭代只发 chunk，
    LLM 那句「好的我去看看」原本永远送不出去。这里在每个工具批次开始时把攒下的中间话术投出去，
    让长任务在微信侧边做边有反馈。终局文本与中间话术天然不相交（message.complete 的 text 是
    最后一轮迭代的输出，不含此前各轮），无需去重。
    """

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.reply_text: str | None = None
        self.error: str | None = None
        self.media: list[dict] = []
        self._on_start: Callable[[], Awaitable[None]] | None = None
        self._on_progress: Callable[[str], Awaitable[None]] | None = None
        self._progress_buffer: list[str] = []
        self._typing_task: asyncio.Task | None = None

    def _clear_typing_task(self, task: asyncio.Task) -> None:
        # 仅当仍是当前 task 时清空：新一轮 typing 已覆盖时，旧回调安全跳过。
        if self._typing_task is task:
            self._typing_task = None
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.warning("channel typing callback raised", exc_info=exc)

    def bind_typing(self, on_start: Callable[[], Awaitable[None]]) -> None:
        self._on_start = on_start

    def bind_progress(self, on_progress: Callable[[str], Awaitable[None]]) -> None:
        self._on_progress = on_progress

    async def aclose(self) -> None:
        """等待 typing 回调退出，防止回合取消后它继续使用已关闭的适配器。"""
        task = self._typing_task
        self._typing_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _take_progress(self) -> str:
        """同步取出并清空缓冲——必须在 await 投递之前完成。

        可并行的工具批次走 asyncio.gather，多个 _execute_single_tool 几乎同时发 tool_start；
        若先 await 再清空，两个协程都会看到同一段非空缓冲并各投一次，微信侧出现重复。
        """
        text = "".join(self._progress_buffer).strip()
        self._progress_buffer.clear()
        return text

    async def send_json(self, data: dict) -> None:
        self.frames.append(data)
        frame_type = data.get("type")
        if frame_type == "message.start" and self._on_start is not None:
            self._typing_task = asyncio.create_task(self._on_start())
            self._typing_task.add_done_callback(self._clear_typing_task)
        elif frame_type == "chunk":
            self._progress_buffer.append(data.get("content", ""))
        elif frame_type == "tool_start" and self._on_progress is not None:
            if pending := self._take_progress():
                await self._on_progress(pending)
        elif frame_type == "message.complete":
            self._progress_buffer.clear()
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
        reply = await _execute_im_turn(adapter, [item.msg for item in batch])
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


async def _execute_im_turn(adapter: ChannelAdapter, batch: list[InboundMessage]) -> str | None:
    """跑一轮完整 chat turn（自带 emitter，不依赖用户 WS——桌面离线也能回），把回复格式化后经渠道送出。

    沿 _execute_cron_turn 的回合先例（connection.py），差异在 emitter：cron 复用用户 WS 派发器（桌面离线
    回合即死），这里无头捕获 + 回合后渠道投递。人设/长期记忆/主动记忆块按 user 加载，与桌面回合共享。
    """
    snapshot = adapter.snapshot
    last = batch[-1]
    base = SETTINGS.public_base_url.strip().rstrip("/")
    messages = []
    for message in batch:
        attachments = []
        for attachment in message.attachments:
            url = attachment.url
            if url.startswith("/api/media/files/") and base:
                url = f"{base}{url}"
            attachments.append(
                {"type": attachment.type, "file_url": url, **({"name": attachment.name} if attachment.name else {})},
            )
        messages.append({"text": message.text, "attachments": attachments})
    last_message = messages[-1]
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
        session_id = str(conv.id)
        # 排队合并：前导消息先落库（与 prompt.submit 的 batch 前导同构），末条驱动本轮。
        if len(messages) > 1:
            await persist_extra_user_messages(db, conv.id, messages[:-1])

    emitter = ChannelTurnEmitter()
    if adapter.supports_typing:

        async def _typing_on() -> None:
            try:
                await adapter.send_typing(last.peer_id, last.context_token, status=1)
            except Exception:
                logger.debug("typing indicator failed", extra={"binding": snapshot.id})

        emitter.bind_typing(_typing_on)

    async def _progress(text: str) -> None:
        # 严格 best-effort：tool_start 的发射点在 _execute_single_tool 的 try 之外，异常会沿 send_json
        # 冒泡把工具记成崩溃结果——微信偶发超时/频控/token 瞬时失效绝不能拖垮本机任务。
        try:
            if clean := strip_markdown(text):
                for chunk in chunk_text(clean, SETTINGS.weixin_reply_max_chars):
                    await adapter.send_text(last.peer_id, chunk, last.context_token)
        except Exception:
            logger.debug("progress relay failed", extra={"binding": snapshot.id})

    emitter.bind_progress(_progress)

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
            content=last_message["text"],
            attachments=last_message["attachments"] or None,
        ),
        client_context=client_context,
    )
    try:
        await run_chat_turn(req, llm_config, snapshot.user_id, emitter)
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
    delivered = False
    chunks = chunk_text(plain, SETTINGS.weixin_reply_max_chars)
    media = emitter.media
    if media:
        # 媒体合并到第一条 chunk（同一条 iLink sendmessage）；若失败则后续 chunk 降级为纯文本。
        head = chunks[0] if chunks else None
        try:
            await adapter.send_media(last.peer_id, head, media, last.context_token)
            delivered = True
            for chunk in chunks[1:]:
                try:
                    await adapter.send_text(last.peer_id, chunk, last.context_token)
                    delivered = True
                except Exception:
                    logger.exception(
                        "reply chunk delivery failed",
                        extra={"binding": snapshot.id, "channel": snapshot.channel},
                    )
        except Exception:
            logger.exception(
                "media reply delivery failed; falling back to text",
                extra={"binding": snapshot.id, "channel": snapshot.channel},
            )
            for chunk in chunks:
                try:
                    await adapter.send_text(last.peer_id, chunk, last.context_token)
                    delivered = True
                except Exception:
                    logger.exception(
                        "reply delivery failed",
                        extra={"binding": snapshot.id, "channel": snapshot.channel},
                    )
    else:
        for chunk in chunks:
            try:
                await adapter.send_text(last.peer_id, chunk, last.context_token)
                delivered = True
            except Exception:
                logger.exception("reply delivery failed", extra={"binding": snapshot.id, "channel": snapshot.channel})
    if not delivered:
        try:
            await adapter.send_text(last.peer_id, _DELIVERY_FAILED_FALLBACK, last.context_token)
        except Exception:
            logger.error("fallback delivery also failed", extra={"binding": snapshot.id})
    return plain
