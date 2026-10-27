"""外观模块公开的身份引用。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.appearance_ports import AppearanceReadiness
from features.companion.queries import portrait_is_confirmed

from .models import AvatarAsset, CompanionOutfit, OutfitSource
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


async def avatar_snapshot(db: AsyncSession, user_id: int, avatar_id: int) -> AvatarSnapshot | None:
    row = await db.scalar(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.id == avatar_id))
    return AvatarSnapshot.model_validate(row) if row is not None else None


def outfit_identity_matches(source_json: dict[str, Any], identity_reference_path: str) -> bool:
    """只核对全身来源；角色卡文字修订不改变身体身份。"""
    source = OutfitSource.model_validate(source_json)
    return bool(identity_reference_path) and source.identity_reference_path == identity_reference_path


async def ready_outfit_snapshot(db: AsyncSession, user_id: int, outfit_id: int) -> OutfitSnapshot | None:
    row = await db.scalar(
        select(CompanionOutfit).where(
            CompanionOutfit.user_id == user_id,
            CompanionOutfit.id == outfit_id,
            CompanionOutfit.status == "ready",
        ),
    )
    return OutfitSnapshot.model_validate(row) if row is not None else None


async def initial_outfit_snapshot(db: AsyncSession, user_id: int) -> OutfitSnapshot | None:
    row = await db.scalar(
        select(CompanionOutfit)
        .where(
            CompanionOutfit.user_id == user_id,
            CompanionOutfit.status == "ready",
            CompanionOutfit.is_initial.is_(True),
        )
        .order_by(CompanionOutfit.id)
        .limit(1),
    )
    return OutfitSnapshot.model_validate(row) if row is not None else None


async def ready_outfit_snapshots(db: AsyncSession, user_id: int, *, limit: int = 50) -> list[OutfitSnapshot]:
    rows = await db.scalars(
        select(CompanionOutfit)
        .where(
            CompanionOutfit.user_id == user_id,
            CompanionOutfit.status == "ready",
        )
        .order_by(CompanionOutfit.id.desc())
        .limit(max(1, min(limit, 100))),
    )
    return [OutfitSnapshot.model_validate(row) for row in rows]
