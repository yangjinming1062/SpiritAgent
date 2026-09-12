import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from components import safe_json_loads


@dataclass(frozen=True)
class InboundAttachment:
    """入站附件（图片/语音/文件）：url 是后端 temp-media 公网地址（`/api/media/files/...`），type 与现有
    ChatRequest 附件约定一致（image / video）；LLM 看到的就是 input_image/input_video。"""

    type: str  # "image" | "file" | "voice"  (LLM 侧按 image 消费 voice)
    url: str
    name: str = ""


@dataclass(frozen=True)
class InboundMessage:
    """适配器归一化后的入站消息：channel 是注册表键，peer_id 是渠道侧对端标识（如微信 wxid）。"""

    channel: str
    peer_id: str
    peer_name: str
    text: str
    # 微信 iLink 的回复凭据（reply-only：send 必须回显入站消息携带的 token）；其它渠道为 None。
    context_token: str | None = None
    is_group: bool = False
    attachments: tuple[InboundAttachment, ...] = ()


@dataclass(frozen=True)
class ChannelBindingSnapshot:
    """绑定的标量快照：适配器长任务不得持有 ORM 行（session 关闭后访问属性会 DetachedInstanceError），
    manager 在每次（重）启动时从新鲜 DB 行拍照传入。"""

    id: int
    user_id: int
    channel: str
    config: dict = field(default_factory=dict)
    credentials: str = ""


class ChannelError(Exception):
    """适配器错误：fatal=True 表示该绑定不可恢复（守卫循环停任务、标 error），否则退避后重建适配器重试。"""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


# 入站回调：入队并返回 per-message future（turn 完成时以回复文本 resolve）；适配器可选择等待或 fire-and-forget。
OnInbound = Callable[[InboundMessage], Awaitable[asyncio.Future[str | None]]]


class ChannelAdapter:
    """外部 IM 渠道适配器基类：run() 是常驻循环（轮询/WS 重连/空转），send_text 承担出站投递。

    生命周期由 ChannelManager 的守卫任务驱动：run() 抛 fatal ChannelError → 绑定标 error 停止；
    非 fatal 异常 → 记日志、退避 channels_restart_backoff_seconds 后重建适配器重试。
    """

    channel_name: str = ""
    # 桌面端 im 会话标题（如 "微信对话"）。
    conversation_title: str = ""
    supports_typing: bool = False
    supports_media: bool = False
    # 能否在无入站消息时主动发起（微信 iLink reply-only → False；其它可主动的通道 → True）。
    can_initiate: bool = False
    requires_login: bool = False

    def has_credentials(self) -> bool:
        """requires_login 渠道据此区分启动即连与等待登录；无需登录的渠道恒 True。"""
        return True

    def __init__(self, snapshot: ChannelBindingSnapshot, on_inbound: OnInbound) -> None:
        self.snapshot = snapshot
        self._on_inbound = on_inbound
        # 适配器派生出的登录、入站分发等任务都归当前绑定实例所有。守卫重建或绑定停止时由
        # aclose 统一取消并等待，避免旧实例越过生命周期边界继续驱动本机工具。
        self._owned_tasks: set[asyncio.Task] = set()

    def create_task(self, coro: Awaitable, *, name: str | None = None) -> asyncio.Task:
        """创建归当前适配器实例所有的子任务。"""
        task = asyncio.create_task(coro, name=name)
        self._owned_tasks.add(task)
        task.add_done_callback(self._owned_tasks.discard)
        return task

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
        media: list[dict],
        context_token: str | None = None,
    ) -> None:
        """出站媒体（与回复文本合并成一条消息）；默认回退到仅文本，媒体被丢弃。"""
        if text:
            await self.send_text(peer_id, text, context_token=context_token)

    async def send_typing(self, peer_id: str, context_token: str | None = None, status: int = 1) -> None:
        """默认 no-op：不支持 typing 的渠道静默跳过。"""

    async def start_login(self) -> None:
        """默认 no-op：无需扫码登录的渠道没有登录态。"""

    async def login_state(self) -> dict:
        """默认无登录流：REST 轮询登录状态时返回 {"state": "unsupported"}。"""
        return {"state": "unsupported"}

    async def logout(self) -> None:
        """默认 no-op。"""

    def platform_hint(self) -> str | None:
        """注入 system prompt 的 PLATFORM_HINTS 键（weixin/qqbot…）；无渠道人设差异时返回 None。"""
        return None

    @staticmethod
    def parse_config(raw: str | None) -> dict:
        parsed = safe_json_loads(raw or "{}", default={})
        return parsed if isinstance(parsed, dict) else {}
