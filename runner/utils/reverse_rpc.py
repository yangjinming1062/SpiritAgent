import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

_handler: Callable[..., Awaitable[Any]] | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def set_handler(h: Callable[..., Awaitable[Any]]) -> None:
    global _handler
    _handler = h


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """注册 Runner 主事件循环，供同步上下文桥接使用。"""
    global _main_loop
    _main_loop = loop


async def call_llm(**kwargs: Any) -> str:
    if not _handler:
        raise RuntimeError("Reverse RPC handler not configured; server.py must call set_handler() at startup")
    return await _handler(kwargs)


def call_llm_sync(**kwargs: Any) -> str:
    """在工作线程（同步工具）上下文中调用反向 RPC LLM 处理函数。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("call_llm_sync must not run on an event loop; await call_llm instead")
    if _main_loop is None or not _main_loop.is_running():
        raise RuntimeError("Main event loop not registered; server.py must call set_main_loop() at startup")
    timeout = float(kwargs.get("timeout") or 300)
    fut = asyncio.run_coroutine_threadsafe(call_llm(**kwargs), _main_loop)
    try:
        return fut.result(timeout=timeout + 30)
    except Exception:
        fut.cancel()
        raise
