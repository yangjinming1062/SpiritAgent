import asyncio
import os
import threading
from dataclasses import dataclass
from typing import Any

from spirit_agent_runner.contracts.rpc import RpcError, ToolExecuteRequest
from spirit_agent_runner.contracts.scope import SkillScope
from spirit_agent_runner.contracts.tooling import ToolError

from . import journal
from .config import ConfigStore
from .tools import ToolExecutor


@dataclass(frozen=True)
class InflightCall:
    task: asyncio.Task[Any]
    cancel_event: threading.Event
    dispatched: bool


class CallManager:
    def __init__(self, executor: ToolExecutor, config: ConfigStore) -> None:
        self.executor = executor
        self.config = config
        self._inflight: dict[tuple[str, str], InflightCall] = {}
        self._closing = False

    def cancel(self, connection_id: str, req_id: object = None) -> dict[str, Any]:
        if req_id is None:
            calls = [
                call
                for (connection, _), call in self._inflight.items()
                if connection == connection_id and call.dispatched
            ]
        else:
            call = self._inflight.get((connection_id, str(req_id)))
            calls = [call] if call else []
        for call in calls:
            call.cancel_event.set()
            call.task.cancel()
        return {"ok": True} if calls else {"ok": False, "error": "no_in_flight"}

    async def execute(self, connection_id: str, req_id: object, request: ToolExecuteRequest) -> Any:
        if self._closing:
            raise RpcError(-32000, "Runner is stopping", {"disposition": "failed"})
        name, args, call_id = request.name, request.args, request.call_id
        scope = (
            SkillScope(request.skill_scope.user_id, request.skill_scope.system_preset_id)
            if request.skill_scope is not None
            else None
        )
        try:
            context = self.config.context(call_id=call_id, skill_scope=scope)
            self.executor.catalog.definition(name, context)
        except (ValueError, ToolError) as error:
            raise RpcError(-32000, str(error), {"disposition": "failed"}) from error
        key = connection_id, str(req_id)
        if key in self._inflight:
            raise RpcError(-32000, "RPC request is already in flight", {"disposition": "failed"})
        task = asyncio.current_task()
        assert task is not None
        self._inflight[key] = InflightCall(task, context.cancel_event, dispatched=call_id is not None)
        claim_task: asyncio.Task[journal.ClaimOutcome] | None = None
        claim_token = None
        try:
            if call_id:
                claim_task = asyncio.create_task(
                    asyncio.to_thread(journal.claim, call_id, name, args, os.getpid(), scope),
                )
                outcome = await asyncio.shield(claim_task)
                claim_token = outcome.claim_token
                if not outcome.should_execute:
                    if outcome.disposition == "completed":
                        return outcome.result
                    raise RpcError(
                        -32011 if outcome.disposition == "unknown" else -32000,
                        outcome.error or f"call_id {call_id!r} rejected: {outcome.disposition}",
                        {"disposition": outcome.disposition},
                    )
            try:
                result = await self.executor.invoke(context, name, args)
            except ToolError as error:
                if call_id and claim_token is not None:
                    await asyncio.to_thread(journal.mark_failed, call_id, claim_token, str(error))
                raise RpcError(-32000, str(error), {"disposition": "failed"}) from error
            if call_id and claim_token is not None:
                await asyncio.to_thread(journal.mark_completed, call_id, claim_token, result)
            return result
        except asyncio.CancelledError:
            context.cancel_event.set()
            if call_id and claim_task is not None:
                await journal.settle_cancelled_claim(call_id, claim_task)
            raise
        finally:
            self._inflight.pop(key, None)

    async def close(self) -> None:
        self._closing = True
        calls = list(self._inflight.values())
        for call in calls:
            call.cancel_event.set()
            call.task.cancel()
        if calls:
            await asyncio.gather(*(call.task for call in calls), return_exceptions=True)
