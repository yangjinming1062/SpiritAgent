"""确认图决定视觉身份；版本只随用户的明确采纳推进。"""

from uuid import uuid4

from infrastructure.ai.prompt_engineer import VisualReasoningError
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.appearance.asset_bindings import flush_appearance
from features.appearance.models import (
    PREPARATION_RESUMABLE_STATES,
    AppearancePreparationJob,
    AvatarAsset,
    VisualObservationJob,
)
from features.appearance.queries import initial_outfit_snapshot
from features.media.identity_contracts import VisualIdentitySnapshot

IDENTITY_NOT_CONFIRMED = "请先确认头像和全身形象"


class VisualIdentityNotReadyError(VisualReasoningError):
    pass


async def get_visual_identity_asset(db: AsyncSession, user_id: int, *, lock: bool = False) -> AvatarAsset | None:
    if any(
        isinstance(row, (AvatarAsset, AppearancePreparationJob)) and row.user_id == user_id
        for row in set(db.new) | set(db.dirty)
    ):
        await flush_appearance(db, user_id)
    query = (
        select(AvatarAsset)
        .where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True))
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update(of=AvatarAsset)
    return await db.scalar(query)


def identity_snapshot(asset: AvatarAsset) -> VisualIdentitySnapshot:
    return VisualIdentitySnapshot(avatar_id=asset.id, revision=asset.identity_revision)


async def load_visual_identity(db: AsyncSession, user_id: int) -> VisualIdentitySnapshot | None:
    asset = await get_visual_identity_asset(db, user_id)
    if (
        asset is None
        or not asset.is_fullbody_confirmed
        or asset.identity_revision < 1
        or not asset.asset_url
        or not asset.seed_fullbody_url
    ):
        return None
    return identity_snapshot(asset)


async def require_visual_identity(db: AsyncSession, user_id: int) -> VisualIdentitySnapshot:
    identity = await load_visual_identity(db, user_id)
    if identity is None:
        raise VisualIdentityNotReadyError(IDENTITY_NOT_CONFIRMED)
    return identity


async def visual_identity_is_current(db: AsyncSession, user_id: int, snapshot: VisualIdentitySnapshot) -> bool:
    asset = await get_visual_identity_asset(db, user_id, lock=True)
    return (
        asset is not None
        and asset.is_fullbody_confirmed
        and asset.id == snapshot.avatar_id
        and asset.identity_revision == snapshot.revision
    )


def emit_visual_identity_updated(db: AsyncSession, asset: AvatarAsset) -> None:
    emit_ws_event(
        db,
        user_id=asset.user_id,
        event_type="companion.visual_identity.updated",
        payload={"avatar_id": asset.id, "revision": asset.identity_revision},
    )
    emit_ws_event(
        db,
        user_id=asset.user_id,
        event_type="companion.life.presentation_requested",
        payload={"force": True},
    )


async def initialize_visual_identity(db: AsyncSession, avatar: AvatarAsset) -> None:
    previous = avatar.preparation_job
    if previous is not None and previous.state in PREPARATION_RESUMABLE_STATES:
        previous.state, previous.error = "cancelled", "确认形象已更新"
    avatar.identity_revision += 1
    initial_outfit = await initial_outfit_snapshot(db, avatar.user_id)
    avatar.preparation_job = (
        AppearancePreparationJob(
            user_id=avatar.user_id,
            avatar_id=avatar.id,
            preparation_id=str(uuid4()),
            identity_revision=avatar.identity_revision,
            portrait_source_path=avatar.asset_url,
            body_source_path=avatar.seed_fullbody_url,
            state="queued",
        )
        if initial_outfit is None or not initial_outfit.fullbody_url
        else None
    )
    db.add(
        VisualObservationJob(
            user_id=avatar.user_id,
            avatar_id=avatar.id,
            identity_revision=avatar.identity_revision,
            portrait_source_path=avatar.asset_url,
            body_source_path=avatar.seed_fullbody_url,
            state="queued",
        )
    )
    emit_visual_identity_updated(db, avatar)


def emit_preparation_updated(db: AsyncSession, user_id: int, avatar_id: int) -> None:
    emit_ws_event(
        db, user_id=user_id, event_type="companion.appearance_preparation.updated", payload={"avatar_id": avatar_id}
    )
