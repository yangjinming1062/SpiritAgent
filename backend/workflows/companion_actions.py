"""伙伴统一动作视图与工具用例；画面选择冻结后只向原目标派发。"""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from features.accounts.models import lock_user_row
from features.action_library.queries import definition_snapshot, targets_for_definitions
from features.action_library.schemas import ActionDefinitionDesign, ActionSourceScope
from features.action_library.service import accept_definition
from features.companion.disturbance import get_disturbance_tier
from features.companion.proactive_runtime import (
    PresentationSnapshot,
    get_presentation_snapshot,
    get_user_proactive_record,
)
from features.desktop_life.companion_actions import (
    DesktopExpressionCatalog,
    desktop_autonomous_design_available,
    desktop_expression_catalog,
)
from features.desktop_life.repository import DesktopVideoError
from features.desktop_life.schemas import DesktopVideoPlayRequest
from features.desktop_life.service import play_desktop_action
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import resolve_prompt_text
from pydantic import ValidationError
from runtime.tools.presentation import ACTION_TOOL_NAMES
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.action_library import action_variant_reader, schedule_action_definition
from workflows.companion_action_prompts import ACTION_CONTEXT_GUIDANCE, ACTION_UNAVAILABLE_GUIDANCE

ActionTrigger = Literal["chat", "periodic", "user_return"]
_LIVE_PRESENTATION = object()
_STALE_MESSAGE = "当前形象或动作已变化，请重新查看当前动作清单"


@dataclass(frozen=True)
class ActionViewItem:
    action_ref: str
    name: str
    motion_description: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    kind: Literal["once", "loop"]
    duration_seconds: float
    action_id: int
    definition_id: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "action_ref": self.action_ref,
            "name": self.name,
            "motion_description": self.motion_description,
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "kind": self.kind,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class ActionViewSnapshot:
    user_id: int
    presentation: PresentationSnapshot | None
    target_id: int | None = None
    target_epoch: int = 0
    state_version: int = 0
    outfit_id: int | None = None
    source_visual_key: str = ""
    source_scope: ActionSourceScope = "outfit"
    items: tuple[ActionViewItem, ...] = ()
    current_action_ref: str | None = None
    trigger: ActionTrigger = "chat"
    contact_revision: int = 0

    @property
    def available_tool_names(self) -> frozenset[str]:
        return ACTION_TOOL_NAMES if self.target_id is not None else frozenset()

    def to_prompt_block(self, language: str) -> str:
        if not self.available_tool_names:
            return resolve_prompt_text(ACTION_UNAVAILABLE_GUIDANCE, language)
        return (
            resolve_prompt_text(ACTION_CONTEXT_GUIDANCE, language)
            + "\n"
            + json.dumps(
                {
                    "creation_scope": "current_desktop" if self.source_scope == "target" else "shared_outfit",
                    "available_actions": [item.to_dict() for item in self.items],
                },
                ensure_ascii=False,
            )
        )


def _reference(user_id: int, presentation: PresentationSnapshot, target_id: int, epoch: int, action_id: int) -> str:
    raw = json.dumps([user_id, presentation.mode, presentation.revision, target_id, epoch, action_id])
    return "act_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def build_action_view(
    db: AsyncSession,
    user_id: int,
    *,
    presentation: PresentationSnapshot | None | object = _LIVE_PRESENTATION,
    trigger: ActionTrigger = "chat",
    lock: bool = False,
) -> ActionViewSnapshot:
    observed = get_presentation_snapshot(user_id) if presentation is _LIVE_PRESENTATION else presentation
    current = observed if isinstance(observed, PresentationSnapshot) else None
    record = get_user_proactive_record(user_id)
    empty = ActionViewSnapshot(user_id, current, trigger=trigger, contact_revision=record.contact_revision)
    if (
        current is None
        or current != get_presentation_snapshot(user_id)
        or not record.available
        or current.mode != "desktop"
    ):
        return empty
    catalog = await desktop_expression_catalog(
        db,
        user_id,
        presentation_revision=current.revision,
        trigger=trigger,
        lock=lock,
    )
    current_action_id = catalog.current_action_id if catalog is not None else None
    if catalog is None or current != get_presentation_snapshot(user_id) or not record.available:
        return empty
    definitions = {row.definition_id for row in catalog.items if row.definition_id is not None}
    excluded = {
        target.definition_id
        for target in await targets_for_definitions(
            db,
            user_id,
            definitions,
            target_id=catalog.target_id,
            refresh=True,
        )
        if target.state == "excluded"
    }
    if current != get_presentation_snapshot(user_id) or not record.available:
        return empty
    items = tuple(
        ActionViewItem(
            action_ref=_reference(user_id, current, catalog.target_id, catalog.epoch, row.action_id),
            name=row.name,
            motion_description=row.motion_description,
            use_when=row.use_when,
            avoid_when=row.avoid_when,
            kind=row.kind,
            duration_seconds=row.duration_seconds,
            action_id=row.action_id,
            definition_id=row.definition_id,
        )
        for row in catalog.items
        if row.definition_id not in excluded
    )
    return ActionViewSnapshot(
        user_id=user_id,
        presentation=current,
        target_id=catalog.target_id,
        target_epoch=catalog.epoch,
        state_version=catalog.state_version,
        outfit_id=catalog.outfit_id,
        source_visual_key=catalog.source_visual_key,
        source_scope=catalog.scope if isinstance(catalog, DesktopExpressionCatalog) else "outfit",
        items=items,
        current_action_ref=next((item.action_ref for item in items if item.action_id == current_action_id), None),
        trigger=trigger,
        contact_revision=empty.contact_revision,
    )


def _same_target(expected: ActionViewSnapshot, current: ActionViewSnapshot) -> bool:
    return expected.target_id is not None and (
        expected.user_id,
        expected.presentation,
        expected.target_id,
        expected.target_epoch,
        expected.outfit_id,
        expected.source_visual_key,
        expected.source_scope,
    ) == (
        current.user_id,
        current.presentation,
        current.target_id,
        current.target_epoch,
        current.outfit_id,
        current.source_visual_key,
        current.source_scope,
    )


def _live(view: ActionViewSnapshot | None, user_id: int) -> bool:
    return (
        view is not None
        and view.user_id == user_id
        and view.target_id is not None
        and (view.presentation == get_presentation_snapshot(user_id) and get_user_proactive_record(user_id).available)
    )


async def _current_view(
    db: AsyncSession,
    user_id: int,
    view: ActionViewSnapshot | None,
    *,
    lock: bool = False,
) -> ActionViewSnapshot | None:
    if not _live(view, user_id):
        return None
    current = await build_action_view(db, user_id, presentation=view.presentation, trigger=view.trigger, lock=lock)
    return current if _same_target(view, current) else None


async def search_action_reference(
    user_id: int,
    action_view: ActionViewSnapshot | None,
    *,
    query: str = "",
    limit: int = 10,
) -> dict[str, object]:
    if not isinstance(query, str) or type(limit) is not int:
        return {"error": "动作查询须为文字，limit 须为整数"}
    async with SESSION_LOCAL() as db:
        current = await _current_view(db, user_id, action_view)
    if current is None or action_view is None or not _live(action_view, user_id):
        return {"error": _STALE_MESSAGE}
    allowed = {item.action_ref for item in current.items}
    needle = query.strip().casefold()
    items = [
        item
        for item in action_view.items
        if item.action_ref in allowed
        and needle
        in "\n".join(
            (
                item.name,
                item.motion_description,
                *item.use_when,
                *item.avoid_when,
            ),
        ).casefold()
    ]
    return {"hits": [item.to_dict() for item in items[: max(1, min(limit, 20))]], "total": len(items)}


async def inspect_action_reference(
    user_id: int,
    action_view: ActionViewSnapshot | None,
    *,
    action_ref: str | None = None,
    definition_id: int | None = None,
) -> dict[str, object]:
    if (action_ref is None) == (definition_id is None):
        return {"error": "请仅指定 action_ref 或 definition_id 其中一个"}
    if (
        action_ref is not None
        and not isinstance(action_ref, str)
        or definition_id is not None
        and (type(definition_id) is not int or definition_id < 1)
    ):
        return {"error": "动作引用须为文字，设计标识须为正整数"}
    async with SESSION_LOCAL() as db:
        current = await _current_view(db, user_id, action_view, lock=True)
        if current is None or action_view is None:
            return {"error": _STALE_MESSAGE}
        if action_ref is not None:
            captured = next((item for item in action_view.items if item.action_ref == action_ref), None)
            item = next((item for item in current.items if item.action_ref == action_ref), None)
            if captured is None:
                return {"error": "这个动作不在本轮动作清单中"}
            local = None
            if captured.definition_id is not None:
                targets = await targets_for_definitions(
                    db,
                    user_id,
                    (captured.definition_id,),
                    target_id=current.target_id,
                    refresh=True,
                )
                target = next(iter(targets), None)
                if not _live(action_view, user_id):
                    return {"error": _STALE_MESSAGE}
                if target is not None and target.state == "excluded":
                    return {
                        "action_ref": action_ref,
                        "name": captured.name,
                        "available": False,
                        "status": "excluded",
                        "reason": "这个动作已从当前形象移除",
                    }
                local = await action_variant_reader(db, target) if target is not None else None
                if await _current_view(db, user_id, action_view) is None:
                    return {"error": _STALE_MESSAGE}
            if item is None:
                return {
                    "action_ref": action_ref,
                    "name": captured.name,
                    "available": False,
                    "status": local.status if local is not None else "unavailable",
                    "reason": (local.reason if local is not None else None)
                    or "这个动作当前不可用，请重新查看当前动作清单",
                }
            return {
                **item.to_dict(),
                "available": True,
                "status": local.status if local is not None else "ready",
                "reason": local.reason if local is not None else None,
            }
        definition = await definition_snapshot(db, user_id, definition_id)
        if definition is None or (
            definition.outfit_id != current.outfit_id
            or definition.source_visual_key != current.source_visual_key
            or definition.scope == "target"
            and definition.origin_target_id != current.target_id
        ):
            return {"error": "找不到属于当前形象的动作设计"}
        canonical_id = definition.reuse_definition_id or definition.id
        targets = await targets_for_definitions(
            db,
            user_id,
            (canonical_id,),
            target_id=current.target_id,
            refresh=True,
        )
        target = next(iter(targets), None)
        if not _live(action_view, user_id):
            return {"error": _STALE_MESSAGE}
        if target is not None and target.state == "excluded":
            return {
                "definition_id": definition.id,
                "name": definition.name,
                "available": False,
                "status": "excluded",
                "reason": "这个动作已从当前形象移除",
            }
        local = await action_variant_reader(db, target) if target is not None else None
        item = next((item for item in current.items if item.definition_id == canonical_id), None)
        if await _current_view(db, user_id, action_view) is None:
            return {"error": _STALE_MESSAGE}
        return {
            "definition_id": definition.id,
            "name": definition.name,
            "available": item is not None,
            "status": local.status if local is not None else definition.status,
            "reason": (local.reason if local is not None else None) or definition.review_reason,
            **({"action_ref": item.action_ref} if item is not None else {}),
        }


async def design_action_reference(
    user_id: int,
    action_view: ActionViewSnapshot | None,
    *,
    name: str,
    motion_description: str,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    clip_kind: str = "once",
    duration_seconds: int = 4,
    reason: str = "",
    proactive_turn: bool = False,
) -> dict[str, object]:
    try:
        design = ActionDefinitionDesign(
            name=name,
            motion_description=motion_description,
            use_when=use_when or [],
            avoid_when=avoid_when or [],
            clip_kind=clip_kind,
            duration_seconds=duration_seconds,
            reason=reason,
        )
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors(include_input=False) if error["loc"]})
        return {"error": "动作设计参数不合法，请检查：" + "、".join(fields)}
    async with SESSION_LOCAL() as db:
        await lock_user_row(db, user_id)
        current = await _current_view(db, user_id, action_view)
        if current is None:
            return {"error": _STALE_MESSAGE}
        if (
            proactive_turn
            and current.presentation.mode == "desktop"
            and not await desktop_autonomous_design_available(
                db,
                user_id,
                current.target_id,
            )
        ):
            return {"error": "自主创建动作当前已关闭"}
        accepted = await accept_definition(
            db,
            user_id,
            design,
            source="autonomous" if proactive_turn else "user_requested",
            expected_target_id=current.target_id,
        )
        if await _current_view(db, user_id, action_view) is None:
            await db.rollback()
            return {"error": _STALE_MESSAGE}
        await db.commit()
    if accepted.definition_id and accepted.status != "rejected":
        schedule_action_definition(user_id, accepted.definition_id)
    return {
        "ok": accepted.status != "rejected",
        "definition_id": accepted.definition_id or None,
        "status": accepted.status,
        "message": accepted.message
        if accepted.status == "rejected"
        else (
            "动作设计已登记，可用后可以表演"
            if accepted.status in {"approved", "reused"}
            else "动作设计已受理，等待评审与制作"
        ),
    }


async def play_action_reference(
    user_id: int,
    action_ref: str,
    action_view: ActionViewSnapshot | None,
    *,
    source: Literal["chat_expression", "autonomous"] = "chat_expression",
    reason: str = "",
) -> dict[str, object]:
    if not _live(action_view, user_id) or not isinstance(action_ref, str):
        return {"error": _STALE_MESSAGE}
    if not isinstance(reason, str):
        return {"error": "动作播放原因须为文字"}
    reason = reason[:200]
    captured = next((item for item in action_view.items if item.action_ref == action_ref), None)
    if captured is None:
        return {"error": "这个动作不在本轮可用动作中"}
    async with SESSION_LOCAL() as db:
        current = await _current_view(db, user_id, action_view, lock=True)
        if current is None or not any(item.action_ref == action_ref for item in current.items):
            return {"error": _STALE_MESSAGE}
        if source == "autonomous" and not await _autonomous_current(db, user_id, action_view, current):
            return {"error": "当前情境已变化，本次动作已取消"}
        try:
            command = await play_desktop_action(
                db,
                user_id,
                DesktopVideoPlayRequest(
                    action_id=captured.action_id,
                    expected_set_id=current.target_id,
                    reason=reason,
                ),
                source=source,
            )
        except DesktopVideoError as exc:
            return {"error": str(exc)}
        response = {"outcome": "queued", "play_id": command.play_id, "played": False}
        final_view = await _current_view(db, user_id, action_view)
        if (
            final_view is None
            or source == "autonomous"
            and not await _autonomous_current(
                db,
                user_id,
                action_view,
                final_view,
            )
        ):
            await db.rollback()
            return {"error": "当前情境已变化，本次动作已取消"}
        await db.commit()
    return {**response, "action_ref": action_ref}


async def _autonomous_current(
    db: AsyncSession,
    user_id: int,
    expected: ActionViewSnapshot,
    current: ActionViewSnapshot,
) -> bool:
    tier = await get_disturbance_tier(user_id, db=db)
    record = get_user_proactive_record(user_id)
    return (
        record.available
        and not record.busy_count
        and record.contact_revision == expected.contact_revision
        and current.state_version == expected.state_version
        and _live(expected, user_id)
        and tier == "autonomous"
    )
