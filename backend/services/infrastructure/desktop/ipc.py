import asyncio
import contextlib
import json
from typing import Any

from components import SETTINGS

from .connection import MANAGER

_PENDING: dict[tuple[int, str], asyncio.Future[str]] = {}


def _outcome_unknown_result(message: str) -> str:
    """与客户端回传的结果未知同一信封（ok=false + error），循环守卫按失败计数，模型按正文核对而不是重做。"""
    return json.dumps({"ok": False, "error": message})


_DESKTOP_GONE_ERROR = _outcome_unknown_result(
    "Desktop disconnected before the tool call completed, so the outcome is unknown: the tool may or may "
    "not have run. Do not rerun it automatically; check its effects or ask the user first.",
)


async def dispatch_device_call(user_id: int, call_id: str, payload: dict[str, Any]) -> str | None:
    """向用户桌面派发 tool.call 并等待 tool.result；桌面无 dispatcher 或入队失败时返回 None。等待对象先于派发登记（毫秒级工具结果可能早于入队返回），之后直接持对象等待。"""
    dispatcher = MANAGER.get_dispatcher(user_id)
    if dispatcher is None or not MANAGER.is_connected(user_id):
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
            # 超时不代表未执行，工具可能仍在本机运行：按结果未知告知模型，由其核对实际效果或询问用户，不能直接重做。
            return _outcome_unknown_result(
                f"Tool execution timeout for call {call_id} (no response within {timeout}s), so the outcome is "
                "unknown: the tool may or may not have run, or may still be running. Do not rerun it "
                "automatically; check its effects or ask the user first.",
            )
    except asyncio.CancelledError:
        # 回合被中断（停止对话等）时请桌面取消这次调用；取消只是请求，不撤销已发生的本机副作用。
        if (current := MANAGER.get_dispatcher(user_id)) is not None:
            await current.enqueue_event("tool.cancel", {"call_id": call_id})
        raise
    finally:
        # 覆盖成功 / 未入队 / 超时 / 外部取消：不清理会在 _PENDING 里留下永久句柄。
        if _PENDING.get(key) is fut:
            del _PENDING[key]


def resolve_future(user_id: int, call_id: str, result: str) -> bool:
    fut = _PENDING.pop((user_id, call_id), None)
    if fut is None or fut.done():
        return False
    fut.set_result(result)
    return True


def discard_user(user_id: int) -> None:
    """以「桌面离线」错误兑现该用户所有未决等待。刻意不用 cancel()：CancelledError 继承 BaseException，会穿透 chat 回合各层的 except Exception 导致回合静默死亡；用错误兑现能让回合正常收尾并如实告知。"""
    for key in [k for k in _PENDING if k[0] == user_id]:
        fut = _PENDING.pop(key)
        # 与 wait_for 的取消存在竞态：done() 与 set_result 之间 future 可能已被取消；单条失败不能中断整轮清理
        if not fut.done():
            with contextlib.suppress(asyncio.InvalidStateError):
                fut.set_result(_DESKTOP_GONE_ERROR)
