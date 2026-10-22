from collections.abc import Sequence

from infrastructure.assets.references import asset_paths
from infrastructure.text import utc_now
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.settings.values import get_user_setting

from .queries import live_video_asset_references
from .voice_models import CustomVoice
from .voice_queries import custom_voice_tokens


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced = asset_paths([row.model_dump() for row in await live_video_asset_references(db, user_id)], user_id)
    selected_tokens = custom_voice_tokens(await get_user_setting(db, user_id, "companion.voice_id"))
    for row in await db.scalars(
        select(CustomVoice).where(
            CustomVoice.user_id == user_id,
            CustomVoice.is_current.is_(True)
            | (CustomVoice.expires_at > utc_now())
            | CustomVoice.token.in_(selected_tokens),
        ),
    ):
        referenced |= asset_paths([row.reference_path, row.preview_path], user_id)
    return referenced


async def cleanup_obsolete_assets(db: AsyncSession, user_id: int) -> set[str]:
    released: set[str] = set()
    expired = list(
        await db.scalars(
            select(CustomVoice).where(
                CustomVoice.user_id == user_id,
                CustomVoice.version == 0,
                CustomVoice.expires_at <= utc_now(),
            ),
        ),
    )
    for row in expired:
        released |= asset_paths([row.reference_path, row.preview_path], user_id)
        await db.delete(row)
    return released


async def forget_deleted_assets(db: AsyncSession, user_id: int, paths: Sequence[str]) -> None:
    if not paths:
        return
    rows = await db.scalars(
        select(CustomVoice).where(
            CustomVoice.user_id == user_id,
            CustomVoice.reference_path.in_(paths) | CustomVoice.preview_path.in_(paths),
        ),
    )
    for row in rows:
        if row.reference_path in paths:
            row.reference_path = None
        if row.preview_path in paths:
            row.preview_path = ""
