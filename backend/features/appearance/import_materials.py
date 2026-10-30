"""衣柜对用户影像提取结果的受控入库；调用方持有采纳事务。"""

import asyncio

from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.domain_locks import lock_domain
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.media.identity_contracts import CharacterCardSnapshot

from .asset_bindings import flush_appearance
from .avatar_service import validate_transparent_portrait
from .character_card import character_snapshot_is_current
from .models import CompanionOutfit, OutfitSource
from .query_contracts import OutfitSnapshot


async def accept_imported_outfit(
    db: AsyncSession,
    user_id: int,
    *,
    source_key: str,
    path: str,
    name: str,
    description: str,
    identity: CharacterCardSnapshot,
    identity_path: str,
) -> OutfitSnapshot:
    await lock_domain(db, user_id, "appearance")
    existing = await db.scalar(
        select(CompanionOutfit).where(CompanionOutfit.user_id == user_id, CompanionOutfit.source_key == source_key)
    )
    if existing is not None:
        return OutfitSnapshot.model_validate(existing)
    if not await character_snapshot_is_current(db, user_id, identity):
        raise ValueError("伙伴身份已变化，请重新核对提取的着装")
    resolved = resolve_asset_reference(path)
    if resolved is None:
        raise ValueError("提取的着装图片缺失")
    data = await asyncio.to_thread(resolved[0].read_bytes)
    await asyncio.to_thread(validate_transparent_portrait, data)
    row = CompanionOutfit(
        user_id=user_id,
        source_key=source_key,
        name=name[:64],
        description=description,
        description_status="ready",
        fullbody_url=path,
        status="ready",
        source_json=OutfitSource(
            description=description, character_card_revision=identity.revision, identity_reference_path=identity_path
        ).dump(),
    )
    db.add(row)
    await flush_appearance(db, user_id)
    emit_ws_event(db, user_id=user_id, event_type="companion.outfit.updated", payload={"outfit_id": row.id})
    return OutfitSnapshot.model_validate(row)


async def undo_imported_outfit(db: AsyncSession, user_id: int, material_id: int, source_key: str) -> None:
    row = await db.scalar(
        select(CompanionOutfit).where(CompanionOutfit.user_id == user_id, CompanionOutfit.id == material_id)
    )
    if row is not None:
        if row.source_key != source_key:
            raise ValueError("不能撤销并非由这次影像提取创建的着装")
        await lock_domain(db, user_id, "appearance")
        await db.delete(row)
        await db.flush()
        emit_ws_event(db, user_id=user_id, event_type="companion.outfit.updated", payload={"outfit_id": material_id})
