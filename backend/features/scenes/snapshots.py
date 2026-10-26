"""场景工具读取与操作的公开快照；数据行只在场景模块内流转。"""

from sqlalchemy.ext.asyncio import AsyncSession

from features.life.schemas import LifeStepRequest

from .queries import list_scenes, scene_view
from .query_contracts import SceneSnapshot, SceneSnapshotPage
from .service import schedule_scene_generation


async def list_scene_snapshots(
    db: AsyncSession,
    user_id: int,
    *,
    query: str = "",
    cursor: int | None = None,
    limit: int = 30,
    ready_only: bool = False,
) -> SceneSnapshotPage:
    rows, total, next_cursor = await list_scenes(
        db, user_id, query=query, cursor=cursor, limit=limit, ready_only=ready_only
    )
    return SceneSnapshotPage(
        scenes=rows, total=total, next_cursor=next_cursor, has_more=next_cursor is not None, limit=limit
    )


async def schedule_scene_snapshot(
    user_id: int,
    *,
    origin: str,
    notes: str | None = None,
    reference_image: bytes | None = None,
    life_step: LifeStepRequest | None = None,
) -> SceneSnapshot:
    row = await schedule_scene_generation(
        user_id,
        origin=origin,
        notes=notes,
        reference_image=reference_image,
        life_step=life_step,
    )
    return scene_view(row)
