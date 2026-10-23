"""场景工具读取与操作的公开快照；数据行只在场景模块内流转。"""

from sqlalchemy.ext.asyncio import AsyncSession

from .queries import list_scenes
from .query_contracts import SceneSnapshot
from .service import schedule_scene_generation


async def list_scene_snapshots(
    db: AsyncSession,
    user_id: int,
    *,
    query: str = "",
    offset: int = 0,
    limit: int = 30,
    ready_only: bool = False,
) -> tuple[list[SceneSnapshot], int]:
    rows, total = await list_scenes(db, user_id, query=query, offset=offset, limit=limit, ready_only=ready_only)
    return [SceneSnapshot.model_validate(row) for row in rows], total


async def schedule_scene_snapshot(
    user_id: int,
    *,
    origin: str,
    notes: str | None = None,
    reference_image: bytes | None = None,
) -> SceneSnapshot:
    row = await schedule_scene_generation(
        user_id,
        origin=origin,
        notes=notes,
        reference_image=reference_image,
    )
    return SceneSnapshot.model_validate(row)
