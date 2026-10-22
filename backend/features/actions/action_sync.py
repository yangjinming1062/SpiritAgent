"""共享动作工作流使用的窗口目标快照、占位登记与首次制作。"""

import json
from collections.abc import Sequence

from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.models import lock_user_row
from features.action_library.queries import definition_snapshot, definitions_by_id
from features.action_library.schemas import ActionDefinitionSnapshot, ActionSourceSnapshot, ActionVariantSummary
from features.appearance.queries import active_avatar_snapshot
from features.appearance.query_contracts import AvatarSnapshot

from .generation.errors import VideoPackStateError
from .generation.queries import _job_result_unresolved, _load_generation_context
from .generation.references import _require_current_identity_image, require_action_matting_model
from .generation.tasks import kick_dynamic_action
from .materials import accepted_action_asset
from .models import CompanionAction, CompanionActionPack
from .policy import check_creation_enabled
from .schemas import ActionDesignRequest


async def _window_source_from_pack(
    user_id: int,
    pack: CompanionActionPack,
    avatar: AvatarSnapshot | None,
) -> ActionSourceSnapshot | None:
    if pack.user_id != user_id:
        return None
    context = _load_generation_context(pack)
    reason = None
    if (
        context is None
        or not pack.source_visual_key
        or not pack.reference_path
        or context.reference_alignment != "ready"
    ):
        reason = "动作组的冻结参考尚未准备完成"
    else:
        try:
            await _require_current_identity_image(context, avatar)
        except VideoPackStateError as exc:
            reason = str(exc)
    return ActionSourceSnapshot(
        user_id=user_id,
        outfit_id=pack.outfit_id,
        target_kind="window",
        target_id=pack.id,
        label=context.outfit_description if context else "",
        source_visual_key=pack.source_visual_key,
        ready=reason is None,
        blocked_reason=reason,
        reference_paths=[pack.reference_path] if pack.reference_path else [],
        reference_roles=["完整角色与当前着装，固定身份及造型以图像为准"] if pack.reference_path else [],
        identity=context.identity if context else None,
        outfit_description=context.outfit_description if context else "",
        persona=context.persona_definition if context else {},
    )


async def window_action_source(db: AsyncSession, user_id: int, pack_id: int) -> ActionSourceSnapshot | None:
    pack = await db.get(CompanionActionPack, pack_id)
    if pack is None or pack.user_id != user_id:
        return None
    return await _window_source_from_pack(user_id, pack, await active_avatar_snapshot(db, user_id))


async def window_action_sources(db: AsyncSession, user_id: int) -> tuple[ActionSourceSnapshot, ...]:
    packs = (await db.scalars(select(CompanionActionPack).where(CompanionActionPack.user_id == user_id))).all()
    avatar = await active_avatar_snapshot(db, user_id)
    sources = [await _window_source_from_pack(user_id, pack, avatar) for pack in packs]
    return tuple(source for source in sources if source is not None)


async def active_window_action_source(db: AsyncSession, user_id: int) -> ActionSourceSnapshot | None:
    pack_id = await db.scalar(
        select(CompanionActionPack.id).where(
            CompanionActionPack.user_id == user_id,
            CompanionActionPack.active.is_(True),
        ),
    )
    return await window_action_source(db, user_id, pack_id) if pack_id is not None else None


async def ensure_synced_window_action(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    definition: ActionDefinitionSnapshot,
) -> int:
    await lock_user_row(db, user_id)
    pack = await db.get(CompanionActionPack, pack_id)
    if pack is None or pack.user_id != user_id:
        raise VideoPackStateError("动作组已被移除")
    if (
        definition.user_id != user_id
        or pack.outfit_id != definition.outfit_id
        or pack.source_visual_key != definition.source_visual_key
    ):
        raise VideoPackStateError("动作与着装组的冻结来源不一致")
    action = await db.scalar(
        select(CompanionAction).where(
            CompanionAction.pack_id == pack_id,
            CompanionAction.definition_id == definition.id,
        ),
    )
    if action is None:
        design = ActionDesignRequest(
            name=definition.name,
            motion_description=definition.motion_description,
            use_when=definition.use_when,
            avoid_when=definition.avoid_when,
            clip_kind=definition.clip_kind,
            duration_seconds=definition.duration_seconds,
            reason=definition.reason or "补齐同着装动作版本",
        )
        action = CompanionAction(
            user_id=user_id,
            pack_id=pack_id,
            outfit_id=pack.outfit_id,
            definition_id=definition.id,
            key=f"shared_{definition.id}",
            name=definition.name,
            motion_description=definition.motion_description,
            media_type="video",
            kind=definition.clip_kind,
            target_duration_seconds=definition.duration_seconds,
            loopable=definition.clip_kind == "loop",
            use_when=json.dumps(definition.use_when, ensure_ascii=False),
            avoid_when=json.dumps(definition.avoid_when, ensure_ascii=False),
            status="not_generated",
            stage="design",
            reference_hash=pack.reference_hash,
            source_design_json=design.model_dump_json(),
        )
        db.add(action)
        await db.flush()
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.video.progress",
            payload={
                "packId": pack_id,
                "outfitId": pack.outfit_id,
                "action": action.key,
                "stage": "design",
            },
        )
    return action.id


async def window_target_states(
    db: AsyncSession,
    user_id: int,
    definition_ids: Sequence[int],
) -> dict[int, list[ActionVariantSummary]]:
    result: dict[int, list[ActionVariantSummary]] = {}
    rows = list(
        await db.execute(
            select(CompanionAction, CompanionActionPack)
            .join(
                CompanionActionPack,
                CompanionActionPack.id == CompanionAction.pack_id,
            )
            .where(CompanionAction.user_id == user_id, CompanionAction.definition_id.in_(definition_ids)),
        ),
    )
    wanted = {action.definition_id for action, _ in rows}
    definitions = await definitions_by_id(db, user_id, wanted)
    avatar = await active_avatar_snapshot(db, user_id)
    sources: dict[int, ActionSourceSnapshot | None] = {}
    for action, pack in rows:
        material = accepted_action_asset(action)
        status = "ready" if action.status == "succeeded" and material else action.status
        if not action.enabled:
            status = "disabled"
        if status not in {"not_generated", "queued", "processing", "ready", "failed", "disabled"}:
            status = "blocked"
        if pack.id not in sources:
            sources[pack.id] = await _window_source_from_pack(user_id, pack, avatar)
        source = sources[pack.id]
        can_generate = action.enabled and action.status == "not_generated" and source is not None and source.ready
        definition = definitions.get(action.definition_id)
        source_mismatch = (
            definition is None
            or not pack.source_visual_key
            or definition.outfit_id != pack.outfit_id
            or definition.source_visual_key != pack.source_visual_key
        )
        reason = action.error or (source.blocked_reason if source and not source.ready else None)
        if source_mismatch:
            status, can_generate = "blocked" if action.enabled else "disabled", False
            reason = "动作的冻结身份或着装来源已变化，无法在这个形象中制作"
        result.setdefault(action.definition_id, []).append(
            ActionVariantSummary(
                target_kind="window",
                target_id=pack.id,
                action_id=action.id,
                label=source.label if source else "",
                status=status,
                has_ready_asset=material is not None,
                can_generate=can_generate,
                reason=reason,
            ),
        )
    return result


async def request_synced_window_generation(user_id: int, action_id: int, *, source: str = "user_requested") -> None:
    async with SESSION_LOCAL() as db:
        await lock_user_row(db, user_id)
        action = await db.get(CompanionAction, action_id)
        if action is None or action.user_id != user_id or action.definition_id is None:
            raise VideoPackStateError("找不到对应的共同动作版本")
        pack = await db.get(CompanionActionPack, action.pack_id)
        if pack is not None:
            await db.refresh(pack, with_for_update=True)
        definition = await definition_snapshot(db, user_id, action.definition_id)
        if (
            pack is None
            or definition is None
            or not pack.source_visual_key
            or definition.outfit_id != pack.outfit_id
            or definition.source_visual_key != pack.source_visual_key
        ):
            raise VideoPackStateError("动作的冻结身份或着装来源已变化，无法制作此版本")
        if action.status != "not_generated" or _job_result_unresolved(action) or not action.enabled:
            return
        target = await window_action_source(db, user_id, action.pack_id)
        if target is None or not target.ready:
            raise VideoPackStateError((target.blocked_reason if target else None) or "动作制作来源不可用")
        check_creation_enabled(source)
        require_action_matting_model()
        if pack.status == "failed":
            pack.status, pack.error = "processing", None
        action.status, action.stage, action.error = "queued", "design", None
        await db.commit()
        pack_id = action.pack_id
    kick_dynamic_action(pack_id, action_id, user_id)
