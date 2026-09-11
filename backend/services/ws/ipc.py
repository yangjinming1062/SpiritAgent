import asyncio
import contextlib
import json

from components import JSONRPC_INTERNAL_ERROR, SETTINGS

_PENDING: dict[tuple[int, str], asyncio.Future] = {}

_DESKTOP_GONE_ERROR = json.dumps(
    {"code": JSONRPC_INTERNAL_ERROR, "message": "Desktop disconnected before the tool call completed."},
)


def create_future(user_id: int, call_id: str) -> asyncio.Future:
    fut = asyncio.get_running_loop().create_future()
    _PENDING[(user_id, call_id)] = fut
    return fut


def resolve_future(user_id: int, call_id: str, result: str) -> bool:
    fut = _PENDING.pop((user_id, call_id), None)
    if fut is None or fut.done():
        return False
    fut.set_result(result)
    return True


def discard_user(user_id: int) -> int:
    """以「桌面离线」错误 resolve 该 user 的所有 pending future，返回移除数量。

    刻意不用 ``cancel()``：``CancelledError`` 继承 ``BaseException``，会穿透 chat 回合各层的
    ``except Exception`` 导致回合静默死亡（IM 侧表现为对端永远等不到任何回复）。桌面掉线在语义上
    就是「在飞的设备调用全部以离线失败告终」，用错误 resolve 能让回合正常收尾并如实告知。
    """
    keys = [k for k in _PENDING if k[0] == user_id]
    for k in keys:
        fut = _PENDING.pop(k, None)
        if fut is not None and not fut.done():
            # 与 wait_for 的取消存在竞态：done() 与 set_result 之间 future 可能已被取消。
            # 单条失败不能中断整轮清理，否则其余 future 全部留在挂起态。
            with contextlib.suppress(asyncio.InvalidStateError):
                fut.set_result(_DESKTOP_GONE_ERROR)
    return len(keys)


def discard_call(user_id: int, call_id: str) -> asyncio.Future | None:
    fut = _PENDING.pop((user_id, call_id), None)
    if fut is not None and not fut.done():
        fut.cancel()
    return fut


async def wait_future(user_id: int, call_id: str, fut: asyncio.Future, *, timeout: float | None = None) -> str:
    """等待 ``create_future`` 已注册的 future，超时上限 timeout 秒。

    刻意接收 future 对象而非按键重查：``resolve_future`` 会把条目 pop 掉，若结果早于本函数抵达
    （毫秒级工具的常态），重查必然落空。注册与等待分离是为了让调用方能在派发设备指令**之前**
    完成注册，重查会把这个保证打回原形。
    """
    effective_timeout = timeout if timeout is not None else SETTINGS.ipc_future_timeout_seconds
    try:
        return await asyncio.wait_for(fut, timeout=effective_timeout)
    except TimeoutError:
        return json.dumps(
            {
                "code": JSONRPC_INTERNAL_ERROR,
                "message": f"Tool execution timeout for call {call_id} (no response within {effective_timeout}s). The desktop runner may be offline.",
            },
        )
    finally:
        # 覆盖成功 / 超时 / 外部取消三条路径：成功路径上 resolve_future 已摘走，此处是 no-op；
        # 外部取消（IM 侧中止）不经此清理会在 _PENDING 里留下永久句柄。
        discard_call(user_id, call_id)
