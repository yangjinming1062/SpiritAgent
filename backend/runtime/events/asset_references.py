from infrastructure.assets.references import asset_paths
from sqlalchemy.ext.asyncio import AsyncSession

from .queries import live_event_payloads, remove_events, settled_event_payloads


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    return asset_paths(await live_event_payloads(db, user_id), user_id)


async def cleanup_obsolete_assets(db: AsyncSession, user_id: int) -> set[str]:
    released: set[str] = set()
    removed: list[int] = []
    for event_id, payload in await settled_event_payloads(db, user_id):
        paths = asset_paths(payload, user_id)
        if paths:
            released |= paths
            removed.append(event_id)
    await remove_events(db, user_id, removed)
    return released
