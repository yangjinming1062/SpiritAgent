import asyncio
import os
import platform
import sys
import time
import uuid
from typing import Any

from spirit_agent_runner.contracts.rpc import RpcError
from spirit_agent_runner.infrastructure.constants import get_spiritagent_home
from spirit_agent_runner.version import __version__

from . import journal
from .calls import CallManager
from .capabilities import CapabilityProbe, disk_free_bytes, network_reachable
from .config import ConfigStore
from .tools import ToolExecutor


class RunnerRuntime:
    def __init__(self, config: ConfigStore, executor: ToolExecutor) -> None:
        self.config = config
        self.executor = executor
        self.calls = CallManager(executor, config)
        self.capabilities = CapabilityProbe()
        self.config_lock = asyncio.Lock()
        self.run_generation = uuid.uuid4().hex
        self.started_at = time.time()
        self.reconnect_count = 0
        self.reconnect_streak = 0

    async def ready(self) -> dict[str, Any]:
        caps, health, failed = await asyncio.to_thread(self.capabilities.snapshot)
        return {
            "version": __version__,
            "run_generation": self.run_generation,
            "pid": os.getpid(),
            "capabilities": caps,
            "capabilities_health": health,
            "probe_failed": failed,
            "reconnect_streak": self.reconnect_streak,
        }

    async def handle(self, connection_id: str, req_id: object, method: str, params: dict[str, Any]) -> Any:
        if method in {"execute_tool", "execute_scoped_tool"}:
            return await self.calls.execute(connection_id, req_id, method, params)
        if method == "spiritagent.cancel":
            return self.calls.cancel(connection_id, params.get("req_id"))
        if method == "get_tools":
            async with self.config_lock:
                context = self.config.context()
                return {"tools": await asyncio.to_thread(self.executor.schemas, context)}
        if method == "spiritagent.config.update":
            config = params.get("config")
            if not isinstance(config, dict):
                raise ValueError("spiritagent.config.update requires a 'config' object")
            async with self.config_lock:
                self.config.update(config)
                self.executor.catalog.availability.invalidate()
            return {"ok": True}
        if method == "spiritagent.call_result":
            call_id = params.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("spiritagent.call_result requires a 'call_id' string")
            record = await asyncio.to_thread(journal.lookup, call_id)
            return record.model_dump() if record is not None else {"call_id": call_id, "status": "not_found"}
        if method == "spiritagent.info":
            payload = await self.ready()
            payload.update(
                {
                    "started_at": self.started_at,
                    "uptime_seconds": round(time.time() - self.started_at, 2),
                    "reconnect_count": self.reconnect_count,
                    "tool_count": len(self.executor.catalog.names),
                    "failed_tools": {},
                    "network_reachable": await asyncio.to_thread(network_reachable),
                    "disk_free_bytes": disk_free_bytes(get_spiritagent_home()),
                    "system": {
                        "platform": sys.platform,
                        "python": sys.version.split()[0],
                        "release": platform.release(),
                        "machine": platform.machine(),
                    },
                },
            )
            return payload
        raise RpcError(-32601, "Method not found")

    async def start(self) -> None:
        await asyncio.to_thread(journal.sweep_stale_claims, os.getpid())

    async def close(self) -> None:
        await self.calls.close()
