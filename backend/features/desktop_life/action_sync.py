"""共享动作工作流使用的桌景目标事务与冻结快照。"""

import json
from collections.abc import Sequence

from infrastructure.database import SESSION_LOCAL
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.action_library.identity import definition_matches_source
from features.action_library.queries import definition_snapshot
from features.action_library.schemas import ActionDefinitionSnapshot, ActionSourceSnapshot, ActionVariantSummary
from features.action_library.service import exclude_target

from .models import DesktopVideoAction, DesktopVideoSet
from .queries import desktop_action_source
from .repository import (
    DesktopVideoNotFoundError,
    DesktopVideoStateError,
    desktop_action,
    desktop_action_response,
    desktop_state,
    touch_desktop_state,
)
from .schemas import DesktopVideoActionResponse
from .service import generate_desktop_action


async def desktop_action_sources(db: AsyncSession, user_id: int) -> tuple[ActionSourceSnapshot, ...]:
    ids = list(await db.scalars(select(DesktopVideoSet.id).where(DesktopVideoSet.user_id == user_id)))
    sources = [await desktop_action_source(db, user_id, set_id) for set_id in ids]
    return tuple(source for source in sources if source is not None)


async def ensure_synced_desktop_action(
    db: AsyncSession,
    user_id: int,
    set_id: int,
    definition: ActionDefinitionSnapshot,
) -> int:
    """只登记本地版本；制作意图由共享目录持久化并在事务提交后调度。"""
    await desktop_state(db, user_id, create=True)
    source = await desktop_action_source(db, user_id, set_id)
    if source is None:
        raise DesktopVideoNotFoundError("找不到对应的桌景")
    if definition.user_id != user_id or not definition_matches_source(definition, source):
        raise DesktopVideoStateError("动作与桌景的冻结身份、着装来源不匹配")
    suite = await db.get(DesktopVideoSet, set_id)
    if suite is None:
        raise DesktopVideoNotFoundError("找不到对应的桌景")
    blocked_reason = source.blocked_reason
    action = await db.scalar(
        select(DesktopVideoAction).where(
            DesktopVideoAction.user_id == user_id,
            DesktopVideoAction.set_id == set_id,
            DesktopVideoAction.definition_id == definition.id,
        ),
    )
    if action is None:
        action = DesktopVideoAction(
            user_id=user_id,
            set_id=set_id,
            definition_id=definition.id,
            key=f"shared_{definition.id}",
            name=definition.name,
            description=definition.motion_description,
            kind=definition.clip_kind,
            duration_seconds=definition.duration_seconds,
            target_duration_seconds=definition.duration_seconds,
            continuity="independent",
            allowed_triggers_json=json.dumps(["periodic", "user_return", "chat", "manual"]),
            use_when_json=json.dumps(definition.use_when, ensure_ascii=False),
            avoid_when_json=json.dumps(definition.avoid_when, ensure_ascii=False),
            preset=False,
            enabled=True,
            status="not_generated" if blocked_reason is None else "blocked",
            stage="prepare" if blocked_reason is None else "blocked",
            error=blocked_reason,
            version=0,
            attempt=0,
            needs_update=False,
            update_pending=False,
        )
        db.add(action)
        await db.flush()
        await touch_desktop_state(db, user_id)
    elif (
        blocked_reason is None
        and action.status == "blocked"
        and not action.generation_state_json
        and not action.accepted_asset_json
    ):
        action.status, action.stage, action.error = "not_generated", "prepare", None
        await touch_desktop_state(db, user_id)
    return action.id


async def desktop_target_states(
    db: AsyncSession,
    user_id: int,
    definition_ids: Sequence[int],
) -> dict[int, list[ActionVariantSummary]]:
    result: dict[int, list[ActionVariantSummary]] = {}
    rows = await db.execute(
        select(DesktopVideoAction, DesktopVideoSet.title)
        .join(DesktopVideoSet, DesktopVideoSet.id == DesktopVideoAction.set_id)
        .where(
            DesktopVideoAction.user_id == user_id,
            DesktopVideoAction.definition_id.in_(definition_ids),
        ),
    )
    for action, label in rows:
        response = desktop_action_response(action)
        status = "disabled" if not action.enabled else response.status
        if status == "ready" and not response.playable:
            status = "blocked"
        reason = response.error or (response.unavailable_reason if not response.playable else None)
        can_generate = response.can_generate
        if can_generate or (status == "blocked" and action.generation_state_json is None and response.current is None):
            source = await desktop_action_source(db, user_id, action.set_id)
            if source is None or not source.ready:
                status, can_generate = "blocked", False
                reason = source.blocked_reason if source else "桌景已删除"
            else:
                status, can_generate, reason = "not_generated", action.enabled, None
        result.setdefault(action.definition_id, []).append(
            ActionVariantSummary(
                target_kind="desktop",
                target_id=action.set_id,
                action_id=action.id,
                label=label,
                status=status,
                has_ready_asset=response.current is not None and not response.needs_update,
                can_generate=can_generate,
                reason=reason,
            ),
        )
    return result


async def request_synced_desktop_generation(
    user_id: int,
    action_id: int,
    *,
    source: str = "user_requested",
) -> DesktopVideoActionResponse:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        if action.definition_id is None:
            raise DesktopVideoStateError("这个桌景动作没有关联共同动作")
        snapshot = await desktop_action_source(db, user_id, action.set_id)
        if snapshot is None or not snapshot.ready:
            raise DesktopVideoStateError(snapshot.blocked_reason if snapshot else "桌景已删除")
        definition = await definition_snapshot(db, user_id, action.definition_id)
        if definition is None or not definition_matches_source(definition, snapshot):
            raise DesktopVideoStateError("动作与桌景的制作来源不匹配")
        if action.status == "blocked" and not action.generation_state_json and not action.accepted_asset_json:
            action.status, action.stage, action.error = "not_generated", "prepare", None
            await touch_desktop_state(db, user_id)
            await db.commit()
        if not desktop_action_response(action).can_generate:
            return desktop_action_response(action)
    return await generate_desktop_action(
        user_id,
        action_id,
        source=source,
        expected_draft_id="none",
        auto_continue=True,
    )


async def set_synced_desktop_action_enabled(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    enabled: bool,
) -> None:
    action = await desktop_action(db, user_id, action_id, lock=True)
    action.enabled = enabled
    await touch_desktop_state(db, user_id)


async def exclude_synced_desktop_action(db: AsyncSession, user_id: int, action_id: int) -> None:
    action = await desktop_action(db, user_id, action_id, lock=True)
    if action.definition_id is None:
        raise DesktopVideoStateError("这个桌景动作没有关联共同动作")
    await exclude_target(db, user_id, action.definition_id, "desktop", action.set_id)
    action.enabled = False
    await touch_desktop_state(db, user_id)
