"""伙伴动作视图的桌景适配；不向推理暴露桌景制作与播放内部资料。"""

import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.models import lock_user_row
from features.action_library.schemas import ActionSourceScope
from features.companion.disturbance import get_disturbance_tier
from features.companion.proactive_runtime import get_user_proactive_record

from .models import DesktopVideoAction, DesktopVideoSet
from .policy import desktop_autonomous_block_reason
from .repository import desktop_accepted_asset, desktop_asset_description, desktop_state


@dataclass(frozen=True)
class DesktopExpressionAction:
    action_id: int
    definition_id: int | None
    name: str
    motion_description: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    kind: Literal["once", "loop"]
    duration_seconds: float


@dataclass(frozen=True)
class DesktopExpressionCatalog:
    target_id: int
    epoch: int
    state_version: int
    outfit_id: int | None
    source_visual_key: str
    scope: ActionSourceScope
    current_action_id: int | None
    items: tuple[DesktopExpressionAction, ...]


async def desktop_autonomous_design_available(db: AsyncSession, user_id: int, set_id: int) -> bool:
    state = await desktop_state(db, user_id, lock=True)
    return state is not None and state.current_set_id == set_id and state.autonomous_enabled


async def desktop_expression_catalog(
    db: AsyncSession,
    user_id: int,
    *,
    presentation_revision: int,
    trigger: Literal["chat", "periodic", "user_return"] = "chat",
    lock: bool = False,
) -> DesktopExpressionCatalog | None:
    if lock:
        await lock_user_row(db, user_id)
    state = await desktop_state(db, user_id, lock=lock)
    if state is None or state.current_set_id is None:
        return None
    suite = await db.scalar(
        select(DesktopVideoSet)
        .where(
            DesktopVideoSet.id == state.current_set_id,
            DesktopVideoSet.user_id == user_id,
        )
        .execution_options(populate_existing=True),
    )
    if suite is None:
        return None
    rows = await db.scalars(
        select(DesktopVideoAction)
        .where(
            DesktopVideoAction.set_id == suite.id,
            DesktopVideoAction.user_id == user_id,
            DesktopVideoAction.enabled.is_(True),
        )
        .order_by(DesktopVideoAction.id)
        .execution_options(populate_existing=True),
    )
    can_express = not state.pinned and state.autonomous_enabled
    autonomous = trigger != "chat"
    if autonomous:
        record = get_user_proactive_record(user_id)
        can_express = (
            can_express
            and record.available
            and not record.busy_count
            and (await get_disturbance_tier(user_id, db=db) == "autonomous")
        )
    items: list[DesktopExpressionAction] = []
    for row in rows:
        if not can_express or row.needs_update or trigger not in json.loads(row.allowed_triggers_json):
            continue
        asset = desktop_accepted_asset(row)
        if asset is None:
            continue
        if autonomous and (
            row.id == state.selected_action_id
            or await desktop_autonomous_block_reason(
                db,
                state,
                row,
                asset,
                presentation_revision=presentation_revision,
            )
            is not None
        ):
            continue
        items.append(
            DesktopExpressionAction(
                action_id=row.id,
                definition_id=row.definition_id,
                name=row.name,
                motion_description=desktop_asset_description(row, asset),
                use_when=tuple(json.loads(row.use_when_json)),
                avoid_when=tuple(json.loads(row.avoid_when_json)),
                kind="loop" if row.kind == "loop" else "once",
                duration_seconds=row.duration_seconds,
            ),
        )
    return DesktopExpressionCatalog(
        target_id=suite.id,
        epoch=state.set_epoch,
        state_version=state.version,
        outfit_id=suite.outfit_id,
        source_visual_key=suite.source_visual_key,
        scope="outfit" if suite.source == "ai" else "target",
        current_action_id=state.selected_action_id,
        items=tuple(items),
    )
