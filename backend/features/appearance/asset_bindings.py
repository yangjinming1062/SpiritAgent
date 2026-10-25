"""外观变更事务维护正式资产外键，回收无需扫描制作文档。"""

from infrastructure.assets.references import asset_paths
from sqlalchemy import delete, inspect, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.service import asset_paths_in_session, ensure_assets_in_session

from .models import AppearanceAsset, AvatarAsset, CharacterAnalysisJob, CompanionOutfit, FullbodyCandidate

_REFERENCES = {
    AvatarAsset: ("avatar_id", ("asset_url", "seed_fullbody_url")),
    CharacterAnalysisJob: ("analysis_job_id", ("portrait_source_path", "body_source_path")),
    CompanionOutfit: ("outfit_id", ("fullbody_url", "source_json")),
    FullbodyCandidate: ("candidate_id", ("image_url", "base_fullbody_url")),
}
_PRIMARY_ASSETS = {
    AvatarAsset: {"portrait_asset_id": "asset_url", "fullbody_asset_id": "seed_fullbody_url"},
    CompanionOutfit: {"asset_id": "fullbody_url"},
    FullbodyCandidate: {"asset_id": "image_url", "base_asset_id": "base_fullbody_url"},
}


async def sync_appearance_assets(db: AsyncSession, user_id: int) -> None:
    changed = []
    for row in set(db.new) | set(db.dirty):
        descriptor = _REFERENCES.get(type(row))
        if descriptor is None or row.user_id != user_id:
            continue
        owner_key, fields = descriptor
        state = inspect(row)
        if row not in db.new and not any(state.attrs[field].history.has_changes() for field in fields):
            continue
        paths = asset_paths([getattr(row, field) for field in fields], user_id)
        changed.append((row, owner_key, paths))
    if not changed:
        return
    paths = {path for _, _, values in changed for path in values}
    assets = await ensure_assets_in_session(db, user_id, paths)
    for row, _, _ in changed:
        for field, path_field in _PRIMARY_ASSETS.get(type(row), {}).items():
            setattr(row, field, assets.get(getattr(row, path_field)))
    await db.flush()
    conditions = [getattr(AppearanceAsset, owner_key) == row.id for row, owner_key, _ in changed]
    await db.execute(delete(AppearanceAsset).where(AppearanceAsset.user_id == user_id, or_(*conditions)))
    bindings = [
        {
            "user_id": user_id,
            "avatar_id": None,
            "outfit_id": None,
            "candidate_id": None,
            "analysis_job_id": None,
            owner_key: row.id,
            "asset_id": assets[path],
        }
        for row, owner_key, values in changed
        for path in sorted(values - {getattr(row, field) for field in _PRIMARY_ASSETS.get(type(row), {}).values()})
    ]
    if bindings:
        await db.execute(insert(AppearanceAsset), bindings)


async def commit_appearance(db: AsyncSession, user_id: int) -> None:
    await sync_appearance_assets(db, user_id)
    await db.commit()


async def flush_appearance(db: AsyncSession, user_id: int) -> None:
    await sync_appearance_assets(db, user_id)
    await db.flush()


async def appearance_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    queries = [select(AppearanceAsset.asset_id).where(AppearanceAsset.user_id == user_id)]
    queries.extend(
        select(getattr(model, field)).where(model.user_id == user_id, getattr(model, field).is_not(None))
        for model, fields in _PRIMARY_ASSETS.items()
        for field in fields
    )
    ids = list(await db.scalars(union_all(*queries)))
    return set((await asset_paths_in_session(db, user_id, ids)).values())
