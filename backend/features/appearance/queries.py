"""外观模块公开的只读身份引用。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.appearance_ports import AppearanceReadiness
from features.companion.queries import portrait_is_confirmed

from .models import AvatarAsset, CompanionOutfit
from .query_contracts import AvatarSnapshot, OutfitSnapshot


async def active_identity_reference(db: AsyncSession, user_id: int) -> str | None:
    return await db.scalar(
        select(AvatarAsset.seed_fullbody_url).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)),
    )


async def appearance_readiness(db: AsyncSession, user_id: int) -> AppearanceReadiness | None:
    row = await db.scalar(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
    return AppearanceReadiness(row.seed_fullbody_url, row.is_fullbody_confirmed) if row is not None else None


async def confirmed_media_reference_paths(db: AsyncSession, user_id: int) -> tuple[str, ...]:
    avatar = await db.scalar(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
    if avatar is None:
        return ()
    portrait_confirmed = await portrait_is_confirmed(db, user_id)
    if portrait_confirmed is None:
        return ()
    return tuple(
        path
        for path in (
            avatar.seed_fullbody_url if avatar.is_fullbody_confirmed else None,
            avatar.asset_url if portrait_confirmed else None,
        )
        if path
    )


async def active_avatar_snapshot(db: AsyncSession, user_id: int) -> AvatarSnapshot | None:
    row = await db.scalar(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
    return AvatarSnapshot.model_validate(row) if row is not None else None


async def avatar_snapshot(db: AsyncSession, user_id: int, avatar_id: int) -> AvatarSnapshot | None:
    row = await db.scalar(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.id == avatar_id))
    return AvatarSnapshot.model_validate(row) if row is not None else None


async def active_outfit_id(db: AsyncSession, user_id: int) -> int | None:
    return await db.scalar(
        select(CompanionOutfit.id).where(CompanionOutfit.user_id == user_id, CompanionOutfit.active.is_(True)),
    )


async def outfit_snapshot(db: AsyncSession, user_id: int, outfit_id: int | None) -> OutfitSnapshot | None:
    if outfit_id is None:
        return None
    row = await db.scalar(
        select(CompanionOutfit).where(CompanionOutfit.user_id == user_id, CompanionOutfit.id == outfit_id),
    )
    return OutfitSnapshot.model_validate(row) if row is not None else None


async def ready_outfit_snapshot(db: AsyncSession, user_id: int, outfit_id: int | None = None) -> OutfitSnapshot | None:
    query = select(CompanionOutfit).where(CompanionOutfit.user_id == user_id, CompanionOutfit.status == "ready")
    query = (
        query.where(CompanionOutfit.id == outfit_id)
        if outfit_id is not None
        else query.where(CompanionOutfit.active.is_(True))
    )
    row = await db.scalar(query)
    return OutfitSnapshot.model_validate(row) if row is not None else None


async def mark_initial_video_started(db: AsyncSession, user_id: int, outfit_id: int) -> None:
    row = await db.scalar(
        select(CompanionOutfit).where(CompanionOutfit.user_id == user_id, CompanionOutfit.id == outfit_id),
    )
    if row is None:
        raise ValueError("Outfit is no longer available")
    row.initial_video_started = True
    row.initial_video_error = None
