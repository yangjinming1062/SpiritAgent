"""夜间场景读取和制作门禁，由场景模块保留 ORM 与状态归属。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionScene, SceneOrigin, SceneStatus
from .queries import get_pending_scene_task, get_scene, get_scene_state, scene_environment
from .service import activate_scene, schedule_scene_generation


@dataclass(frozen=True)
class SceneSnapshot:
    id: int
    user_id: int
    status: str
    title: str
    description: str
    activated_at: datetime | None


@dataclass(frozen=True)
class ScenePlanningSnapshot:
    environment: dict[str, Any]
    generation_pending: bool


def _snapshot(row: CompanionScene) -> SceneSnapshot:
    return SceneSnapshot(row.id, row.user_id, row.status, row.title, row.description, row.activated_at)


async def planning_scene(db: AsyncSession, user_id: int) -> ScenePlanningSnapshot:
    state = await get_scene_state(db, user_id)
    return ScenePlanningSnapshot(scene_environment(state), state.pending is not None or state.regenerating is not None)


async def ready_scenes(db: AsyncSession, user_id: int) -> list[SceneSnapshot]:
    rows = await db.scalars(
        select(CompanionScene)
        .where(CompanionScene.user_id == user_id, CompanionScene.status == SceneStatus.READY.value)
        .order_by(CompanionScene.id.desc()),
    )
    return [_snapshot(row) for row in rows]


async def scene_snapshot(db: AsyncSession, user_id: int, scene_id: int) -> SceneSnapshot | None:
    row = await get_scene(db, user_id, scene_id)
    return _snapshot(row) if row is not None else None


async def find_pending_scene_id(db: AsyncSession, user_id: int) -> int | None:
    row = await get_pending_scene_task(db, user_id)
    return row.id if row is not None else None


async def activate_nightly_scene(db: AsyncSession, user_id: int, scene_id: int) -> SceneSnapshot:
    return _snapshot(await activate_scene(db, user_id, scene_id, origin=SceneOrigin.NIGHTLY.value))


async def schedule_nightly_scene(user_id: int, notes: str) -> int:
    row = await schedule_scene_generation(user_id, origin=SceneOrigin.NIGHTLY.value, notes=notes, auto_activate=True)
    return row.id
