import asyncio
import contextlib
import json
import logging
import random
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from spirit_agent_runner.features.browser.dialog_manager import DialogBlockedError, PendingDialog
from spirit_agent_runner.features.browser.engine.launcher import NativeBrowserProcess

logger = logging.getLogger(__name__)
_CDP_BACKOFF_MAX = 10.0
_DIALOG_BLOCK_GRACE_S = 1.0
_NAVIGATION_METHODS = frozenset({"Page.navigate", "Page.navigateToHistoryEntry", "Page.reload"})


class CdpConnection:
    """Own one CDP socket, event-loop thread, requests and reconnect lifecycle."""

    def __init__(
        self,
        task_id: str,
        cdp_url: str,
        *,
        launch_handle: NativeBrowserProcess | None,
        auto_owned: bool,
        on_connect: Callable[[], Awaitable[None]],
        handle_event: Callable[[str, dict[str, Any], str | None], None],
        pending_dialog: Callable[..., PendingDialog | None],
    ) -> None:
        self.task_id = task_id
        self.cdp_url = cdp_url
        self.launch_handle = launch_handle
        self.auto_owned = auto_owned
        self._on_connect = on_connect
        self._handle_event = handle_event
        self._pending_dialog = pending_dialog
        self._state_lock = threading.Lock()
        self._active = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._main_task: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._start_error: BaseException | None = None
        self._stop_requested = False
        self._ws: Any = None
        self._next_call_id = 1
        self._pending_calls: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._dialog_open_waiters: set[asyncio.Future[None]] = set()

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    @property
    def stopped(self) -> bool:
        return self._stop_requested

    def notify_dialog_open(self) -> None:
        for waiter in self._dialog_open_waiters:
            if not waiter.done():
                waiter.set_result(None)

    def start(self, timeout: float = 15.0) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_requested = False
            self._ready_event.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._thread_main,
                name=f"cdp-supervisor-{self.task_id}",
                daemon=True,
            )
            self._thread.start()

        if not self._ready_event.wait(timeout=timeout):
            self.stop()
            raise TimeoutError(f"CDPSupervisor failed to connect to {self.cdp_url} within {timeout}s")
        if self._start_error is not None:
            err = self._start_error
            self.stop()
            raise err

    def stop(self) -> None:
        """取消 CDP 主任务并等待线程退出；自有浏览器随后终止。可重复调用。"""
        self._stop_requested = True
        loop, task = self._loop, self._main_task
        if loop is not None and task is not None:
            with contextlib.suppress(RuntimeError):  # loop 已关闭
                loop.call_soon_threadsafe(task.cancel)

        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("CDP supervisor %s thread did not exit within 5s", self.task_id)

        with self._state_lock:
            self._active = False

        if self.auto_owned and self.launch_handle is not None:
            self.launch_handle.terminate()

    @property
    def active(self) -> bool:
        """CDP 连接已建立且初始页已附着；断线重连期间为 False。"""
        return self._active

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._main_task = loop.create_task(self._run(), name=f"cdp-supervisor-{self.task_id}")
        self._loop = loop
        try:
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass  # stop() 取消主任务
        except Exception as e:
            if not self._ready_event.is_set():
                self._start_error = e
            else:
                logger.warning("CDP supervisor %s crashed: %s", self.task_id, e)
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            with self._state_lock:
                self._active = False
            if not self._ready_event.is_set():
                self._start_error = self._start_error or RuntimeError("CDP supervisor stopped before connecting")
                self._ready_event.set()

    async def _run(self) -> None:
        backoff = 0.5
        while not self._stop_requested:
            try:
                ws = await asyncio.wait_for(
                    websockets.connect(self.cdp_url, max_size=50 * 1024 * 1024, close_timeout=2.0),
                    timeout=10.0,
                )
            except Exception as e:
                if not self._ready_event.is_set():
                    raise
                logger.warning("CDP supervisor %s reconnect failed: %s", self.task_id, e)
                if self._launched_browser_exited():
                    return
                await asyncio.sleep(random.uniform(0.0, backoff))
                backoff = min(backoff * 2, _CDP_BACKOFF_MAX)
                continue

            self._ws = ws
            reader_task = asyncio.create_task(self._read_loop(ws), name="cdp-reader")
            try:
                await self._on_connect()
                with self._state_lock:
                    self._active = True
                backoff = 0.5
                self._ready_event.set()

                await reader_task
            except Exception as e:
                if not self._ready_event.is_set():
                    raise
                logger.warning("CDP supervisor %s session dropped: %s", self.task_id, e)
            finally:
                with self._state_lock:
                    self._active = False
                if not reader_task.done():
                    reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader_task
                for fut in list(self._pending_calls.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("CDP connection lost"))
                self._pending_calls.clear()
                self._ws = None
                with contextlib.suppress(Exception):
                    await ws.close()

            if self._launched_browser_exited():
                return
            await asyncio.sleep(random.uniform(0.0, backoff))
            backoff = min(backoff * 2, _CDP_BACKOFF_MAX)

    def _launched_browser_exited(self) -> bool:
        """自行启动的浏览器进程已退出（崩溃或用户关闭窗口）时不再重连，由下次导航重新启动。"""
        if not self.auto_owned or self.launch_handle is None or (code := self.launch_handle.poll()) is None:
            return False
        logger.warning("CDP supervisor %s: browser process exited with code %s; not reconnecting", self.task_id, code)
        return True

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("Supervisor WebSocket is not connected")
        call_id = self._next_call_id
        self._next_call_id += 1
        payload: dict[str, Any] = {"id": call_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id

        pre_existing = None
        if method != "Page.handleJavaScriptDialog":
            pre_existing = self._pending_dialog(session_id)
            # 弹窗未决时输入会被丢弃。
            if pre_existing is not None and method.startswith("Input."):
                raise DialogBlockedError(pre_existing, opened_by_call=False)

        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_calls[call_id] = fut
        try:
            await self._ws.send(json.dumps(payload))
            if method == "Page.handleJavaScriptDialog":
                return await asyncio.wait_for(fut, timeout=timeout)
            tolerated = pre_existing.id if pre_existing is not None and method in _NAVIGATION_METHODS else None
            return await self._await_response(
                fut,
                session_id,
                timeout,
                pre_existing_id=pre_existing.id if pre_existing is not None else None,
                tolerated_id=tolerated,
            )
        finally:
            self._pending_calls.pop(call_id, None)

    async def _await_response(
        self,
        fut: asyncio.Future[dict[str, Any]],
        session_id: str | None,
        timeout: float,
        *,
        pre_existing_id: str | None,
        tolerated_id: str | None,
    ) -> dict[str, Any]:
        """等 CDP 响应；弹窗阻塞抛 DialogBlockedError。tolerated_id 为不判阻塞的已有弹窗。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if (dialog := self._pending_dialog(session_id, exclude_id=tolerated_id)) is not None:
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), min(_DIALOG_BLOCK_GRACE_S, max(0.0, remaining)))
                except TimeoutError:
                    raise DialogBlockedError(dialog, opened_by_call=dialog.id != pre_existing_id) from None
            opened: asyncio.Future[None] = loop.create_future()
            self._dialog_open_waiters.add(opened)
            try:
                done, _ = await asyncio.wait(
                    {fut, opened},
                    timeout=max(0.0, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                self._dialog_open_waiters.discard(opened)
                opened.cancel()
            if fut in done:
                return fut.result()
            if not done:
                raise TimeoutError(f"CDP response not received within {timeout}s")

    async def _read_loop(self, ws: Any) -> None:
        """分派 CDP 响应与事件；连接异常关闭时抛出，由 _run 记录并重连。单个事件处理失败只记录，不断开连接。"""
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                logger.debug("CDP supervisor %s dropped non-JSON frame", self.task_id)
                continue

            if "id" in msg:
                fut = self._pending_calls.pop(msg["id"], None)
                if fut is not None and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(f"CDP error on id={msg['id']}: {msg['error']}"))
                    else:
                        fut.set_result(msg)
            elif "method" in msg:
                try:
                    self._handle_event(msg["method"], msg.get("params", {}), msg.get("sessionId"))
                except Exception:
                    logger.exception("CDP supervisor %s failed to handle %s", self.task_id, msg["method"])
