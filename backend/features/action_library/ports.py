"""目标模块的读取与提交后调度由 bootstrap 显式装配。"""

from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import ActionSourceSnapshot, ActionTargetKind, ActionTargetSnapshot, ActionVariantSummary

type SourceReader = Callable[[AsyncSession, int, ActionTargetKind, int | None], Awaitable[ActionSourceSnapshot | None]]
type VariantReader = Callable[[AsyncSession, Sequence[ActionTargetSnapshot]], Awaitable[list[ActionVariantSummary]]]
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


async def read_variants(db: AsyncSession, targets: Sequence[ActionTargetSnapshot]) -> list[ActionVariantSummary]:
    """按目标集合一次读取；返回与 targets 同序等长，targets 须同属一个账户，缺失行由读取实现兜底合成。"""
    if not targets:
        return []
    if _variant_reader is None:
        raise RuntimeError("共享动作版本读取尚未装配")
    return await _variant_reader(db, targets)


def schedule_definition(user_id: int, definition_id: int) -> None:
    if _definition_scheduler is None:
        raise RuntimeError("共享动作调度尚未装配")
    _definition_scheduler(user_id, definition_id)


async def synchronize_target(user_id: int, kind: ActionTargetKind, target_id: int) -> None:
    if _target_sync_handler is None:
        raise RuntimeError("共享动作目标同步尚未装配")
    await _target_sync_handler(user_id, kind, target_id)
