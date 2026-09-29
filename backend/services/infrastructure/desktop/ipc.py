import asyncio
import contextlib
import json
from typing import Any

from components import JSONRPC_INTERNAL_ERROR, SETTINGS

from .connection import MANAGER

_PENDING: dict[tuple[int, str], asyncio.Future[str]] = {}

_DESKTOP_GONE_ERROR = json.dumps(
    {
        "code": JSONRPC_INTERNAL_ERROR,
        "message": "Desktop disconnected before the tool call completed. The outcome is uncertain: do not blindly retry; "
        "verify via the runner call journal (call_id) or ask the user before re-running.",
    },
)


async def dispatch_device_call(user_id: int, call_id: str, payload: dict[str, Any]) -> str | None:
    """向用户桌面派发 ``tool.call`` 设备指令并等待对应 ``tool.result``；桌面无 dispatcher 或入队失败时返回 None。

    等待对象先于派发登记：毫秒级工具的结果可能早于入队返回就抵达，晚登记会被 ``resolve_future`` 当作未知
    call_id 丢弃；之后直接持对象等待，不按键重查（``resolve_future`` 已把条目摘走）。
    """
    dispatcher = MANAGER.get_dispatcher(user_id)
    if dispatcher is None:
        return None
    key = (user_id, call_id)
    fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    _PENDING[key] = fut
    try:
        # writer 吞掉发送异常，WS 掉线只体现为入队返回 False；不看返回值会白等满超时。
        if not await dispatcher.enqueue_event("tool.call", payload):
            return None
        timeout = SETTINGS.ipc_future_timeout_seconds
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            # 超时不代表未执行：Runner 调用日志（PROTOCOL「调用日志与未知结果」）按 call_id 记录实际结局，
            # 重试前先核对，避免把可能已落地的副作用再执行一遍。
            return json.dumps(
                {
                    "code": JSONRPC_INTERNAL_ERROR,
                    "message": f"Tool execution timeout for call {call_id} (no response within {timeout}s). "
                    "The desktop runner may be offline; the call outcome is uncertain — verify via the runner call journal "
                    "for this call_id (or ask the user) before retrying.",
                },
            )
    finally:
        # 覆盖成功 / 未入队 / 超时 / 外部取消（如 IM 侧中止）：不清理会在 _PENDING 里留下永久句柄。
        if _PENDING.get(key) is fut:
            del _PENDING[key]


def resolve_future(user_id: int, call_id: str, result: str) -> bool:
    fut = _PENDING.pop((user_id, call_id), None)
    if fut is None or fut.done():
        return False
    fut.set_result(result)
    return True


def discard_user(user_id: int) -> None:
    """以「桌面离线」错误兑现该用户所有未决等待。

    刻意不用 ``cancel()``：``CancelledError`` 继承 ``BaseException``，会穿透 chat 回合各层的
    ``except Exception`` 导致回合静默死亡（IM 侧表现为对端永远等不到任何回复）。桌面掉线在语义上
    就是「在飞的设备调用全部以离线失败告终」，用错误兑现能让回合正常收尾并如实告知。
    """
    for key in [k for k in _PENDING if k[0] == user_id]:
        fut = _PENDING.pop(key)
        # 与 wait_for 的取消存在竞态：done() 与 set_result 之间 future 可能已被取消；
        # 单条失败不能中断整轮清理，否则其余 future 全部留在挂起态。
        if not fut.done():
            with contextlib.suppress(asyncio.InvalidStateError):
                fut.set_result(_DESKTOP_GONE_ERROR)
