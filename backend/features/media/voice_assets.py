"""声音方案的文件引用与当前方案在同一事务保存。"""

from collections import defaultdict

from infrastructure.assets.references import asset_paths
from infrastructure.text import ensure_utc, utc_now
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.service import ensure_assets_in_session

from .voice_models import CustomVoice


async def sync_voice_assets(db: AsyncSession) -> None:
    owners: dict[int, list[CustomVoice]] = defaultdict(list)
    for row in set(db.new) | set(db.dirty):
        if not isinstance(row, CustomVoice):
            continue
        changed = inspect(row).attrs
        references_changed = any(changed[field].history.has_changes() for field in ("reference_path", "preview_path"))
        became_live = (row.is_current or row.expires_at is not None and ensure_utc(row.expires_at) > utc_now()) and any(
            changed[field].history.has_changes() for field in ("is_current", "expires_at", "version")
        )
        if row in db.new or references_changed or became_live:
            owners[row.user_id].append(row)
    for user_id, voices in owners.items():
        paths = asset_paths([value for row in voices for value in (row.reference_path, row.preview_path)], user_id)
        ids = await ensure_assets_in_session(db, user_id, paths)
        for row in voices:
            row.reference_asset_id = ids.get(row.reference_path)
            row.preview_asset_id = ids.get(row.preview_path)


async def commit_voices(db: AsyncSession) -> None:
    await sync_voice_assets(db)
    await db.commit()


async def flush_voices(db: AsyncSession) -> None:
    await sync_voice_assets(db)
    await db.flush()
