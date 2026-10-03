import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from components import get_logger
from modules.channels import ChannelDeliveryMedia, ChannelLoginStateResponse

logger = get_logger(__name__)


@dataclass(frozen=True)
class InboundAttachment:
    """已校验的入站图片；内联 data URI 保证历史不依赖临时下载地址。"""

    type: Literal["image"]
    url: str


@dataclass(frozen=True)
class InboundMessage:
    """适配器归一化后的入站消息：peer_id 是渠道侧对端标识（如微信 wxid）。"""

    peer_id: str
    peer_name: str
    text: str
    # 渠道侧消息标识（可用时）；缺失时不去重，避免把合法的重复发言当作重投。
    msg_id: str = ""
    # 微信 iLink 的回复凭据（reply-only：send 必须回显入站消息携带的 token）；其它渠道为 None。
    context_token: str | None = None
    attachments: tuple[InboundAttachment, ...] = ()
    # 需从渠道下载的媒体：桥接层在白名单、去重、中止、频控与容量闸门都放行后才调用，结果并入 attachments 落库。
    fetch_attachments: Callable[[], Awaitable[tuple[InboundAttachment, ...]]] | None = None


@dataclass(frozen=True)
class ChannelBindingSnapshot:
    """绑定的标量快照：适配器长任务不得持有 ORM 行（session 关闭后访问属性会 DetachedInstanceError）；manager 每次（重）启动时从新鲜 DB 行拍照传入。"""

    id: int
    user_id: int
    channel: str
    credentials: str = ""


class ChannelError(Exception):
    """适配器错误：fatal=True 表示该绑定不可恢复（守卫循环停任务、标 error），否则退避后重建适配器重试。"""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


class ChannelAdapter:
    """外部 IM 渠道适配器基类：run() 常驻循环（轮询/WS 重连/空转），入站交给 bridge.handle_inbound，send_text 出站投递。生命周期由 ChannelManager 守卫任务驱动：fatal ChannelError → 标 error 停止；非 fatal → 退避 channels_restart_backoff_seconds 后重建适配器重试。"""

    # 桌面端 im 会话标题（如 "微信对话"）。
    conversation_title: str = ""
    supports_typing: bool = False
    requires_login: bool = False

    def has_credentials(self) -> bool:
        """requires_login 渠道据此区分启动即连与等待登录；无需登录的渠道恒 True。"""
        return True

    def __init__(self, snapshot: ChannelBindingSnapshot) -> None:
        self.snapshot = snapshot
        # 登录、回合与补发等派生任务都归当前绑定实例；守卫重建或绑定停止时由 aclose 统一取消并等待，避免旧实例越过生命周期边界继续驱动本机工具。
        self._owned_tasks: set[asyncio.Task] = set()

    def create_task(self, coro: Coroutine[Any, Any, None], *, name: str | None = None) -> asyncio.Task:
        """创建归当前适配器实例所有的子任务。"""
        task = asyncio.create_task(coro, name=name)
        self._owned_tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._owned_tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.error(
                "channel owned task failed",
                extra={"binding": self.snapshot.id, "task": task.get_name()},
                exc_info=exc,
            )

    async def aclose(self) -> None:
        """取消并等待当前适配器实例派生的全部子任务；可重复调用。"""
        current = asyncio.current_task()
        tasks = [task for task in self._owned_tasks if task is not current]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._owned_tasks.difference_update(tasks)

    async def run(self) -> None:
        """默认空转：只等取消。"""
        await asyncio.Event().wait()

    async def send_text(self, peer_id: str, text: str, context_token: str | None = None) -> None:
        raise NotImplementedError

    async def send_media(
        self,
        peer_id: str,
        text: str | None,
        media: list[ChannelDeliveryMedia],
        context_token: str | None = None,
    ) -> None:
        """未实现媒体投递的渠道明确失败，由桥接层保留媒体补发。"""
        if media:
            raise ChannelError("channel media delivery unsupported")
        if text:
            await self.send_text(peer_id, text, context_token=context_token)

    async def send_typing(self, peer_id: str, context_token: str | None = None) -> None:
        """默认 no-op：不支持 typing 的渠道静默跳过。"""

    async def start_login(self) -> None:
        """默认 no-op：无需扫码登录的渠道没有登录态。"""

    async def login_state(self) -> ChannelLoginStateResponse:
        """默认无登录流：REST 轮询登录状态时返回 unsupported。"""
        return ChannelLoginStateResponse(state="unsupported")

    async def logout(self) -> None:
        """默认 no-op。"""

    def platform_hint(self) -> str | None:
        """注入 system prompt 的 PLATFORM_HINTS 键（weixin/qqbot…）；无渠道人设差异时返回 None。"""
        return None
