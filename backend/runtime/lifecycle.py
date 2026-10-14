"""业务模块声明运行生命周期，装配层登记顺序；运行时不导入业务实现。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from .task_supervisor import TASK_SUPERVISOR

RuntimeOperation = Callable[[], Awaitable[object]]
UserRuntimeOperation = Callable[[int], Awaitable[object]]


@dataclass(frozen=True)
class RuntimeParticipant:
    name: str
    prepare: RuntimeOperation | None = None
    recover: RuntimeOperation | None = None
    resume_user: UserRuntimeOperation | None = None
    invalidate_user: Callable[[int], None] | None = None
    stop_producer: RuntimeOperation | None = None
    settle: RuntimeOperation | None = None
    drain: RuntimeOperation | None = None


class RuntimeLifecycle:
    def __init__(self) -> None:
        self._participants: tuple[RuntimeParticipant, ...] = ()
        self._logger = logging.getLogger(__name__)

    def configure(self, participants: Iterable[RuntimeParticipant]) -> None:
        configured = tuple(participants)
        names = [participant.name for participant in configured]
        if len(names) != len(set(names)):
            raise ValueError("runtime participant names must be unique")
        self._participants = configured

    @property
    def participants(self) -> tuple[RuntimeParticipant, ...]:
        return self._participants

    async def prepare(self) -> None:
        for participant in self._participants:
            if participant.prepare is not None:
                await participant.prepare()

    async def recover(self) -> None:
        for participant in self._participants:
            if participant.recover is not None:
                await participant.recover()

    def invalidate_user(self, user_id: int) -> None:
        for participant in self._participants:
            if participant.invalidate_user is None:
                continue
            try:
                participant.invalidate_user(user_id)
            except Exception:
                self._logger.exception(
                    "failed to invalidate user runtime state",
                    extra={"participant": participant.name, "user_id": user_id},
                )

    async def resume_user(self, user_id: int) -> None:
        for participant in self._participants:
            if participant.resume_user is None:
                continue
            try:
                await participant.resume_user(user_id)
            except Exception:
                self._logger.exception(
                    "failed to resume user runtime work",
                    extra={"participant": participant.name, "user_id": user_id},
                )

    async def _best_effort(self, participant: RuntimeParticipant, operation: RuntimeOperation) -> None:
        try:
            await operation()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("runtime lifecycle operation failed", extra={"participant": participant.name})

    async def stop_producers(self) -> None:
        for participant in self._participants:
            if participant.stop_producer is not None:
                await self._best_effort(participant, participant.stop_producer)

    async def drain(self) -> None:
        """先让已受理回合收尾，再收敛模块任务和所有登记的旁路工作。"""
        for participant in self._participants:
            if participant.settle is not None:
                await self._best_effort(participant, participant.settle)
        draining = tuple(participant for participant in self._participants if participant.drain is not None)
        results = await asyncio.gather(
            *(participant.drain() for participant in draining if participant.drain is not None),
            return_exceptions=True,
        )
        for participant, result in zip(draining, results, strict=True):
            if isinstance(result, BaseException):
                self._logger.error(
                    "runtime task drain failed",
                    extra={"participant": participant.name},
                    exc_info=result,
                )
        await TASK_SUPERVISOR.drain()


RUNTIME_LIFECYCLE = RuntimeLifecycle()
