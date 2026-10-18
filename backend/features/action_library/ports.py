"""目标模块的读取与提交后调度由 bootstrap 显式装配。"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import ActionSourceSnapshot, ActionTargetKind, ActionTargetSnapshot, ActionVariantSummary

type SourceReader = Callable[[AsyncSession, int, ActionTargetKind, int | None], Awaitable[ActionSourceSnapshot | None]]
type VariantReader = Callable[[AsyncSession, ActionTargetSnapshot], Awaitable[ActionVariantSummary | None]]
type DefinitionScheduler = Callable[[int, int], None]
type TargetSyncHandler = Callable[[int, ActionTargetKind, int], Awaitable[None]]

_source_reader: SourceReader | None = None
_variant_reader: VariantReader | None = None
_definition_scheduler: DefinitionScheduler | None = None
_target_sync_handler: TargetSyncHandler | None = None


def configure_action_library_ports(
    *,
    source_reader: SourceReader,
    variant_reader: VariantReader,
    definition_scheduler: DefinitionScheduler,
    target_sync_handler: TargetSyncHandler,
) -> None:
    global _source_reader, _variant_reader, _definition_scheduler, _target_sync_handler
    _source_reader, _variant_reader, _definition_scheduler = source_reader, variant_reader, definition_scheduler
    _target_sync_handler = target_sync_handler


async def read_source(
    db: AsyncSession,
    user_id: int,
    kind: ActionTargetKind,
    target_id: int | None,
) -> ActionSourceSnapshot | None:
    if _source_reader is None:
        raise RuntimeError("共享动作来源读取尚未装配")
    return await _source_reader(db, user_id, kind, target_id)


async def read_variant(db: AsyncSession, target: ActionTargetSnapshot) -> ActionVariantSummary | None:
    if _variant_reader is None:
        raise RuntimeError("共享动作版本读取尚未装配")
    return await _variant_reader(db, target)


def schedule_definition(user_id: int, definition_id: int) -> None:
    if _definition_scheduler is None:
        raise RuntimeError("共享动作调度尚未装配")
    _definition_scheduler(user_id, definition_id)


async def synchronize_target(user_id: int, kind: ActionTargetKind, target_id: int) -> None:
    if _target_sync_handler is None:
        raise RuntimeError("共享动作目标同步尚未装配")
    await _target_sync_handler(user_id, kind, target_id)
