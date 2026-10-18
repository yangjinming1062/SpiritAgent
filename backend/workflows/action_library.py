"""共同动作评审后，按明确冻结目标分别登记与提交制作。"""

import asyncio

from features.accounts.models import lock_user_row
from features.action_library.ports import read_source
from features.action_library.queries import (
    definition_snapshot,
    definitions_for_source,
    recoverable_work,
    target_snapshot,
    variant_summaries,
)
from features.action_library.review import review_definition
from features.action_library.schemas import (
    ActionDefinitionDesign,
    ActionSourceSnapshot,
    ActionTargetKind,
    ActionTargetSnapshot,
    ActionVariantSummary,
)
from features.action_library.service import (
    accept_definition,
    bind_target,
    block_target,
    ensure_target,
    finish_fanout,
    mark_target_dispatched,
    record_review,
    recover_interrupted_reviews,
    release_ready_target,
)
from features.actions.action_sync import (
    active_window_action_source,
    ensure_synced_window_action,
    request_synced_window_generation,
    window_action_source,
    window_action_sources,
    window_target_states,
)
from features.companion.proactive_runtime import get_presentation_snapshot
from features.desktop_life.action_sync import (
    desktop_action_sources,
    desktop_target_states,
    ensure_synced_desktop_action,
    request_synced_desktop_generation,
)
from features.desktop_life.queries import desktop_action_source, desktop_state_snapshot
from features.desktop_life.repository import DesktopVideoStateError
from features.desktop_life.schemas import DesktopVideoDesignRequest, DesktopVideoProposalResponse
from features.desktop_life.service import schedule_pending_desktop_updates
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.background import TaskBag
from runtime.paid_work import GenerationWorkPaused
from runtime.user_maintenance import is_user_in_maintenance, track_user_task
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
_TASKS = TaskBag("action_library")
_DEFINITIONS: set[tuple[int, int]] = set()
_TARGETS: set[tuple[int, int]] = set()


async def action_source_reader(
    db: AsyncSession,
    user_id: int,
    kind: ActionTargetKind,
    target_id: int | None,
) -> ActionSourceSnapshot | None:
    if kind == "window":
        return (
            await active_window_action_source(db, user_id)
            if target_id is None
            else await window_action_source(db, user_id, target_id)
        )
    if target_id is None:
        state = await desktop_state_snapshot(db, user_id)
        target_id = state.current_set_id if state else None
    return await desktop_action_source(db, user_id, target_id) if target_id is not None else None


async def action_variant_reader(db: AsyncSession, target: ActionTargetSnapshot) -> ActionVariantSummary | None:
    reader = window_target_states if target.target_kind == "window" else desktop_target_states
    states = await reader(db, target.user_id, [target.definition_id])
    found = next((item for item in states.get(target.definition_id, []) if item.target_id == target.target_id), None)
    if found is not None:
        return found
    source = await action_source_reader(db, target.user_id, target.target_kind, target.target_id)
    return ActionVariantSummary(
        target_kind=target.target_kind,
        target_id=target.target_id,
        action_id=target.action_id,
        label=source.label if source else "",
        status="blocked" if source is None or not source.ready else "not_generated",
        can_generate=False,
        reason=target.reason or (source.blocked_reason if source else "来源形象已移除"),
    )


def schedule_action_definition(user_id: int, definition_id: int) -> None:
    key = (user_id, definition_id)
    if key in _DEFINITIONS or is_user_in_maintenance(user_id):
        return
    _DEFINITIONS.add(key)
    task = asyncio.create_task(_process_definition(user_id, definition_id), name=f"action.definition.{definition_id}")
    _TASKS.add(task)
    track_user_task(user_id, task, cancel_on_maintenance=False)
    task.add_done_callback(lambda _: _DEFINITIONS.discard(key))


async def _process_definition(user_id: int, definition_id: int) -> None:
    try:
        await review_definition(user_id, definition_id)
        async with SESSION_LOCAL() as db:
            definition = await definition_snapshot(db, user_id, definition_id)
        if definition is not None and definition.status in {"approved", "reused"}:
            await _fanout_definition(user_id, definition.reuse_definition_id or definition.id)
            async with SESSION_LOCAL() as db:
                await finish_fanout(db, user_id, definition_id)
                await db.commit()
    except GenerationWorkPaused:
        async with SESSION_LOCAL() as db:
            await record_review(db, user_id, definition_id, "defer", "动作评审已暂停，请手动重试")
            await db.commit()
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("shared action review or fanout failed", extra={"definition_id": definition_id})
        async with SESSION_LOCAL() as db:
            await record_review(db, user_id, definition_id, "defer", "动作评审或登记未完成，请重试")
            await db.commit()


async def _fanout_definition(user_id: int, definition_id: int, *, only: ActionSourceSnapshot | None = None) -> None:
    async with SESSION_LOCAL() as db:
        definition = await definition_snapshot(db, user_id, definition_id)
        if definition is None or definition.status != "approved":
            return
        sources = (
            (only,)
            if only is not None
            else (*await window_action_sources(db, user_id), *await desktop_action_sources(db, user_id))
        )
    queued: list[int] = []
    for source in sources:
        if source.outfit_id != definition.outfit_id:
            continue
        try:
            async with SESSION_LOCAL() as db:
                await lock_user_row(db, user_id)
                current_source = await action_source_reader(db, user_id, source.target_kind, source.target_id)
                if current_source is None:
                    continue
                source = current_source
                target = await ensure_target(db, user_id, definition_id, source, automatic=True)
                if target.state != "excluded" and source.source_visual_key == definition.source_visual_key:
                    ensure = (
                        ensure_synced_window_action if source.target_kind == "window" else ensure_synced_desktop_action
                    )
                    action_id = await ensure(db, user_id, source.target_id, definition, schedule=target.automatic)
                    await bind_target(db, user_id, target.id, action_id)
                    target = await target_snapshot(db, user_id, target.id)
                    if target is not None and target.state == "blocked" and target.automatic:
                        state = await action_variant_reader(db, target)
                        if state is not None and state.can_generate:
                            await release_ready_target(db, user_id, target.id)
                    target = await target_snapshot(db, user_id, target.id)
                if target is not None and target.state == "pending":
                    queued.append(target.id)
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "shared action version registration blocked",
                extra={"definition_id": definition_id, "target_id": source.target_id},
                exc_info=True,
            )
            async with SESSION_LOCAL() as db:
                await lock_user_row(db, user_id)
                current_source = await action_source_reader(db, user_id, source.target_kind, source.target_id)
                if current_source is None:
                    continue
                source = current_source
                target = await ensure_target(db, user_id, definition_id, source, automatic=True)
                await block_target(db, user_id, target.id, str(exc))
                await db.commit()
    for target_id in queued:
        schedule_action_target(user_id, target_id)


def schedule_action_target(user_id: int, target_id: int) -> None:
    key = (user_id, target_id)
    if key in _TARGETS or is_user_in_maintenance(user_id):
        return
    _TARGETS.add(key)
    task = asyncio.create_task(_dispatch_target(user_id, target_id), name=f"action.target.{target_id}")
    _TASKS.add(task)
    track_user_task(user_id, task, cancel_on_maintenance=False)
    task.add_done_callback(lambda _: _TARGETS.discard(key))


async def _dispatch_target(user_id: int, target_id: int) -> None:
    try:
        async with SESSION_LOCAL() as db:
            target = await target_snapshot(db, user_id, target_id)
            if target is None or target.state != "pending":
                return
            definition = await definition_snapshot(db, user_id, target.definition_id)
        if definition is None:
            return
        if target.action_id is None:
            async with SESSION_LOCAL() as db:
                source = await action_source_reader(db, user_id, target.target_kind, target.target_id)
            if source is None:
                raise ValueError("制作目标已移除")
            await _fanout_definition(user_id, definition.id, only=source)
            async with SESSION_LOCAL() as db:
                target = await target_snapshot(db, user_id, target_id)
            if target is None or target.state != "pending" or target.action_id is None:
                return
        async with SESSION_LOCAL() as db:
            state = await action_variant_reader(db, target)
        if state is not None and state.status == "blocked" and not state.can_generate:
            raise ValueError(state.reason or "制作来源尚未就绪")
        request = (
            request_synced_window_generation if target.target_kind == "window" else request_synced_desktop_generation
        )
        await request(user_id, target.action_id, source=definition.source)
        async with SESSION_LOCAL() as db:
            await mark_target_dispatched(db, user_id, target_id)
            await db.commit()
    except GenerationWorkPaused:
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("shared action target blocked", extra={"target_id": target_id}, exc_info=True)
        async with SESSION_LOCAL() as db:
            await block_target(db, user_id, target_id, str(exc))
            await db.commit()


async def synchronize_action_target(user_id: int, kind: ActionTargetKind, target_id: int) -> None:
    async with SESSION_LOCAL() as db:
        source = await action_source_reader(db, user_id, kind, target_id)
        definitions = (
            await definitions_for_source(db, user_id, source.outfit_id, None)
            if source and source.outfit_id is not None
            else []
        )
    if source is not None:
        for definition in definitions:
            if definition.status == "approved":
                await _fanout_definition(user_id, definition.id, only=source)


async def synchronize_desktop_set(user_id: int, set_id: int) -> None:
    await synchronize_action_target(user_id, "desktop", set_id)


async def update_desktop_actions(user_id: int, set_id: int) -> None:
    await schedule_pending_desktop_updates(user_id, set_id)
    await synchronize_desktop_set(user_id, set_id)


async def design_shared_desktop_action(
    user_id: int,
    request: DesktopVideoDesignRequest,
    *,
    source: str,
    require_desktop: bool,
) -> DesktopVideoProposalResponse:
    presentation = get_presentation_snapshot(user_id)
    if require_desktop and (presentation is None or presentation.mode != "desktop"):
        raise DesktopVideoStateError("当前不是桌面模式")
    async with SESSION_LOCAL() as db:
        if source == "autonomous":
            state = await desktop_state_snapshot(db, user_id)
            if (
                state is None
                or not state.autonomous_enabled
                or (request.expected_set_id is not None and request.expected_set_id != state.current_set_id)
            ):
                raise DesktopVideoStateError("当前桌景已切换或自主制作已关闭")
        acceptance = await accept_definition(
            db,
            user_id,
            ActionDefinitionDesign(
                name=request.name,
                motion_description=request.motion_description,
                use_when=request.use_when,
                avoid_when=request.avoid_when,
                clip_kind=request.kind,
                duration_seconds=request.duration_seconds,
                reason=request.reason,
                desktop_continuity=request.continuity,
                desktop_allowed_triggers=request.allowed_triggers,
            ),
            source=source,
            origin_kind="desktop",
            expected_target_id=request.expected_set_id,
        )
        await db.commit()
        if acceptance.status == "rejected":
            raise DesktopVideoStateError(acceptance.message)
        response = await shared_desktop_proposal(db, user_id, acceptance.definition_id, set_id=request.expected_set_id)
    schedule_action_definition(user_id, acceptance.definition_id)
    return response


async def shared_desktop_proposal(
    db: AsyncSession,
    user_id: int,
    definition_id: int,
    *,
    set_id: int | None = None,
) -> DesktopVideoProposalResponse:
    definition = await definition_snapshot(db, user_id, definition_id)
    if definition is None:
        raise DesktopVideoStateError("找不到对应动作提案")
    source = await read_source(db, user_id, "desktop", set_id)
    if (
        source is None
        or not source.source_visual_key
        or definition.outfit_id != source.outfit_id
        or definition.source_visual_key != source.source_visual_key
    ):
        raise DesktopVideoStateError("动作提案不属于这个画面的着装或冻结形象")
    canonical = (
        await definition_snapshot(db, user_id, definition.reuse_definition_id)
        if definition.reuse_definition_id is not None
        else definition
    )
    if (
        canonical is None
        or canonical.outfit_id != source.outfit_id
        or canonical.source_visual_key != source.source_visual_key
    ):
        raise DesktopVideoStateError("动作资料已变化，请刷新后重试")
    variants = await variant_summaries(db, user_id, canonical.id)
    desktop = next(
        (item for item in variants if item.target_kind == "desktop" and item.target_id == source.target_id),
        None,
    )
    return DesktopVideoProposalResponse(
        proposal_id=definition.id,
        definition_id=definition.id,
        set_id=source.target_id,
        action_id=desktop.action_id if desktop is not None and desktop.status != "excluded" else None,
        status="pending" if definition.status == "pending" else definition.status,
        review_reason=definition.review_reason,
        name=definition.name,
        description=definition.motion_description,
        sync_targets=variants,
    )


async def shared_desktop_proposals(db: AsyncSession, user_id: int, set_id: int) -> list[DesktopVideoProposalResponse]:
    source = await desktop_action_source(db, user_id, set_id)
    if source is None or not source.source_visual_key:
        return []
    result = []
    for definition in await definitions_for_source(db, user_id, source.outfit_id, source.source_visual_key):
        canonical = (
            await definition_snapshot(db, user_id, definition.reuse_definition_id)
            if definition.reuse_definition_id is not None
            else definition
        )
        if (
            canonical is None
            or canonical.outfit_id != source.outfit_id
            or canonical.source_visual_key != source.source_visual_key
        ):
            continue
        variants = await variant_summaries(db, user_id, canonical.id)
        local = next(
            (item for item in variants if item.target_kind == "desktop" and item.target_id == set_id),
            None,
        )
        if (
            definition.status in {"approved", "reused"}
            and local is not None
            and local.has_ready_asset
            and local.status not in {"disabled", "excluded", "blocked"}
        ):
            continue
        result.append(
            DesktopVideoProposalResponse(
                proposal_id=definition.id,
                definition_id=definition.id,
                set_id=set_id,
                action_id=local.action_id if local is not None and local.status != "excluded" else None,
                status=definition.status,
                review_reason=definition.review_reason,
                name=definition.name,
                description=definition.motion_description,
                sync_targets=variants,
            ),
        )
    return result


async def resume_shared_actions(user_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        definitions, targets = await recoverable_work(db, user_id)
    for owner_id, definition_id in definitions:
        schedule_action_definition(owner_id, definition_id)
    for owner_id, target_id in targets:
        schedule_action_target(owner_id, target_id)


async def prepare_shared_actions() -> None:
    async with SESSION_LOCAL() as db:
        await recover_interrupted_reviews(db)
        await db.commit()


async def drain_shared_actions() -> None:
    await _TASKS.drain()
