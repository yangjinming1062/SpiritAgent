import asyncio
import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from infrastructure.assets.asset_store import resolve_companion_asset_path
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.assets.references import asset_paths
from infrastructure.text import utc_now
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.references import ACTION_ASSET_FIELDS, PACK_ASSET_FIELDS, AssetReferencesUnavailable

from .models import ActionAssetRetirement, CompanionAction, CompanionActionPack


async def _catalog_asset_paths(storage_path: str, user_id: int) -> set[str]:
    parsed = parse_companion_asset_path(storage_path)
    try:
        if parsed is None or parsed[0] != user_id:
            raise ValueError("Action catalog path escapes its account")
        resolved = resolve_companion_asset_path(*parsed)
        if resolved is None:
            raise ValueError("Action catalog is missing or unsafe")
        path, _ = resolved
        manifest = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise AssetReferencesUnavailable(f"Action catalog unavailable: {storage_path}") from exc
    return asset_paths(manifest, user_id)


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for model, fields in ((CompanionAction, ACTION_ASSET_FIELDS), (CompanionActionPack, PACK_ASSET_FIELDS)):
        for row in await db.scalars(select(model).where(model.user_id == user_id)):
            referenced |= asset_paths([getattr(row, field, None) for field in fields], user_id)
            if isinstance(row, CompanionActionPack) and row.manifest_path:
                referenced |= await _catalog_asset_paths(row.manifest_path, user_id)
    for row in await db.scalars(
        select(ActionAssetRetirement).where(
            ActionAssetRetirement.user_id == user_id,
            ActionAssetRetirement.retired_at > utc_now() - timedelta(hours=24),
        ),
    ):
        referenced |= asset_paths(row.path, user_id)
        if Path(row.path).name.startswith("action-catalog-") and row.path.endswith(".json"):
            referenced |= await _catalog_asset_paths(row.path, user_id)
    return referenced


async def protected_until(db: AsyncSession, user_id: int) -> dict[str, datetime]:
    return {
        row.path: row.retired_at + timedelta(hours=24)
        for row in await db.scalars(select(ActionAssetRetirement).where(ActionAssetRetirement.user_id == user_id))
    }


async def forget_deleted_assets(db: AsyncSession, user_id: int, paths: Sequence[str]) -> None:
    await db.execute(
        delete(ActionAssetRetirement).where(
            ActionAssetRetirement.user_id == user_id,
            ActionAssetRetirement.path.in_(paths),
        ),
    )
