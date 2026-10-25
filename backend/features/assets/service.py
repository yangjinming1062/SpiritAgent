"""业务事务通过批量接口登记与读取正式资产，不直接访问资产 ORM。"""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass

from infrastructure.assets.asset_store import compute_file_sha256, resolve_companion_asset_path
from infrastructure.assets.paths import parse_companion_asset_path
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Asset


@dataclass(frozen=True, slots=True)
class AssetReference:
    id: int
    path: str
    content_hash: str
    media_type: str
    size_bytes: int
    width: int | None
    height: int | None
    duration_ms: int | None


def _inspect_paths(user_id: int, paths: Collection[str], known: set[str]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for path in paths:
        parsed = parse_companion_asset_path(path)
        if parsed is None or parsed[0] != user_id:
            raise ValueError("资产必须属于当前账户")
        resolved = resolve_companion_asset_path(*parsed)
        if resolved is None:
            raise ValueError("正式资产文件不存在")
        if path not in known:
            file, media_type = resolved
            width = height = None
            if media_type.startswith("image/"):
                with Image.open(file) as image:
                    width, height = image.size
            result.append(
                {
                    "user_id": user_id,
                    "path": path,
                    "content_hash": compute_file_sha256(file),
                    "media_type": media_type,
                    "size_bytes": file.stat().st_size,
                    "width": width,
                    "height": height,
                    "state": "ready",
                }
            )
    return result


async def ensure_asset_references_in_session(
    db: AsyncSession, user_id: int, paths: Collection[str]
) -> dict[str, AssetReference]:
    """登记已写完的账户文件；事务及写入租约由采纳方持有。"""
    unique = sorted(set(paths))
    if not unique:
        return {}
    rows = await db.execute(
        select(
            Asset.path,
            Asset.id,
            Asset.state,
            Asset.content_hash,
            Asset.media_type,
            Asset.size_bytes,
            Asset.width,
            Asset.height,
            Asset.duration_ms,
        )
        .where(Asset.user_id == user_id, Asset.path.in_(unique))
        .order_by(Asset.id)
        .with_for_update()
    )
    records = rows.all()
    existing = {
        row.path: AssetReference(
            row.id, row.path, row.content_hash, row.media_type, row.size_bytes, row.width, row.height, row.duration_ms
        )
        for row in records
        if row.state != "deleted" and row.content_hash is not None
    }
    inspected = {
        path
        for path, ref in existing.items()
        if not ref.media_type.startswith("image/") or ref.width is not None and ref.height is not None
    }
    reactivated = [row.id for row in records if row.state == "cleanup" and row.path in inspected]
    values = await asyncio.to_thread(_inspect_paths, user_id, unique, inspected)
    if reactivated:
        await db.execute(update(Asset).where(Asset.id.in_(reactivated)).values(state="ready", last_error=None))
    if values:
        stmt = insert(Asset).values(values)
        rows = await db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_assets_user_path",
                set_={
                    "state": "ready",
                    "content_hash": stmt.excluded.content_hash,
                    "media_type": stmt.excluded.media_type,
                    "size_bytes": stmt.excluded.size_bytes,
                    "width": stmt.excluded.width,
                    "height": stmt.excluded.height,
                    "last_error": None,
                },
            ).returning(
                Asset.path,
                Asset.id,
                Asset.content_hash,
                Asset.media_type,
                Asset.size_bytes,
                Asset.width,
                Asset.height,
                Asset.duration_ms,
            )
        )
        existing.update(
            {
                row.path: AssetReference(
                    row.id,
                    row.path,
                    row.content_hash,
                    row.media_type,
                    row.size_bytes,
                    row.width,
                    row.height,
                    row.duration_ms,
                )
                for row in rows
            }
        )
    return existing


async def ensure_assets_in_session(db: AsyncSession, user_id: int, paths: Collection[str]) -> dict[str, int]:
    references = await ensure_asset_references_in_session(db, user_id, paths)
    return {path: reference.id for path, reference in references.items()}


async def asset_paths_in_session(db: AsyncSession, user_id: int, asset_ids: Collection[int]) -> dict[int, str]:
    if not asset_ids:
        return {}
    return dict(
        (
            await db.execute(
                select(Asset.id, Asset.path).where(
                    Asset.user_id == user_id, Asset.id.in_(asset_ids), Asset.state != "deleted"
                )
            )
        )
        .tuples()
        .all()
    )
