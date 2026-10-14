import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

import websockets

from spirit_agent_runner.contracts.config import load_config
from spirit_agent_runner.contracts.rpc import RpcError
from spirit_agent_runner.infrastructure.redact import redact_sensitive_text
from spirit_agent_runner.runtime.application import RunnerRuntime

from .desktop import DesktopEndpoint, connect_desktop, read_endpoint

logger = logging.getLogger(__name__)


class RunnerTransport:
    def __init__(self, runtime: RunnerRuntime) -> None:
        self.runtime = runtime
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_ws: Any | None = None
        self._baseline: tuple[dict[str, Any], bool] | None = None

    async def notification(self, ws: Any, method: str, params: dict[str, Any]) -> None:
        await ws.send(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}))

    async def process_request(self, ws: Any, connection_id: str, req: dict[str, Any]) -> None:
        req_id = req.get("id")
        if req_id is None:
            return
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        try:
            params = req.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("RPC params must be an object")
            response["result"] = await self.runtime.handle(connection_id, req_id, req.get("method", ""), params)
        except RpcError as error:
            response["error"] = error.payload()
        except asyncio.CancelledError:
            response["error"] = {"code": -32000, "message": "cancelled"}
            with contextlib.suppress(Exception):
                await ws.send(json.dumps(response))
            raise
        except Exception as error:
            response["error"] = {"code": -32000, "message": redact_sensitive_text(str(error), config=load_config())}
        try:
            await ws.send(json.dumps(response))
        except Exception as error:
            logger.debug("RPC reply could not be delivered: %s", type(error).__name__)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("RPC handler failed: %s", redact_sensitive_text(str(error), config=load_config()))

    async def _watch_capabilities(self) -> None:
        while True:
            await asyncio.sleep(120.0)
            if (ws := self._active_ws) is None:
                continue
            payload = await self.runtime.ready()
            baseline = payload["capabilities"], payload["probe_failed"]
            if baseline == self._baseline:
                continue
            try:
                await self.notification(
                    ws,
                    "runner_capabilities_changed",
                    {
                        "run_generation": self.runtime.run_generation,
                        "capabilities": baseline[0],
                        "probe_failed": baseline[1],
                    },
                )
                self._baseline = baseline
            except Exception:
                logger.debug("Capability notification could not be delivered")

    async def run(self, endpoint: DesktopEndpoint) -> None:
        watcher = asyncio.create_task(self._watch_capabilities())
        current: DesktopEndpoint | None = endpoint
        attempt = 0
        try:
            while True:
                tried_connect = current is not None
                if current is not None:
                    try:
                        connection = await connect_desktop(current)
                        async with connection as ws:
                            self._active_ws = ws
                            connection_id = uuid.uuid4().hex
                            try:
                                ready = await self.runtime.ready()
                                await self.notification(ws, "runner_ready", ready)
                                self._baseline = ready["capabilities"], ready["probe_failed"]
                                attempt = 0
                                self.runtime.reconnect_streak = 0
                                async for message in ws:
                                    try:
                                        data = json.loads(message)
                                    except (json.JSONDecodeError, UnicodeDecodeError):
                                        logger.warning("Invalid JSON frame received")
                                        continue
                                    if not isinstance(data, dict) or "method" not in data:
                                        continue
                                    task = asyncio.create_task(self.process_request(ws, connection_id, data))
                                    self._tasks.add(task)
                                    task.add_done_callback(self._finished)
                            finally:
                                self._active_ws = None
                    except websockets.exceptions.InvalidStatus:
                        current = None
                    except Exception as error:
                        logger.warning(
                            "Desktop IPC connection failed: %s",
                            redact_sensitive_text(str(error), config=load_config()),
                        )
                if new_endpoint := read_endpoint():
                    current = new_endpoint
                if not tried_connect:
                    await asyncio.sleep(1.0)
                    continue
                self.runtime.reconnect_count += 1
                self.runtime.reconnect_streak += 1
                attempt += 1
                await asyncio.sleep(min(2.0 * 2 ** min(attempt - 1, 4), 30.0))
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            await self.runtime.close()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
