"""场景查询与环境上下文；聊天、工具与夜间读取同一真源。"""

from dataclasses import dataclass
from typing import Any

from infrastructure.assets import asset_store
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.visual_state import persona_snapshot
from features.scenes.models import CompanionScene, SceneOrigin, ScenePolicy, SceneStatus
from features.scenes.query_contracts import SceneSnapshot
from features.scenes.schemas import SceneImageDimensions, SceneImageSize, SceneResponse


@dataclass(frozen=True, slots=True)
class SceneState:
    active: SceneSnapshot | None
    policy: ScenePolicy
    pending: SceneSnapshot | None
    regenerating: SceneSnapshot | None
    version: int
    switch_version: int


async def get_scene(db: AsyncSession, user_id: int, scene_id: int) -> CompanionScene | None:
    return await db.scalar(
        select(CompanionScene).where(CompanionScene.id == scene_id, CompanionScene.user_id == user_id),
    )


async def get_pending_scene(db: AsyncSession, user_id: int) -> CompanionScene | None:
    return await db.scalar(
        select(CompanionScene)
        .where(
            CompanionScene.user_id == user_id,
            CompanionScene.status == SceneStatus.PENDING.value,
        )
        .order_by(CompanionScene.id.desc())
        .limit(1),
    )


async def get_regenerating_scene(db: AsyncSession, user_id: int) -> CompanionScene | None:
    return await db.scalar(
        select(CompanionScene)
        .where(
            CompanionScene.user_id == user_id,
            CompanionScene.regeneration_status == "pending",
        )
        .order_by(CompanionScene.id.desc())
        .limit(1),
    )


async def get_pending_scene_task(db: AsyncSession, user_id: int) -> CompanionScene | None:
    return await db.scalar(
        select(CompanionScene)
        .where(
            CompanionScene.user_id == user_id,
            or_(
                CompanionScene.status == SceneStatus.PENDING.value,
                CompanionScene.regeneration_status == "pending",
            ),
        )
        .order_by(CompanionScene.id.desc())
        .limit(1),
    )


async def get_scene_state(db: AsyncSession, user_id: int) -> SceneState:
    persona = await persona_snapshot(db, user_id)
    active = await get_scene(db, user_id, persona.active_scene_id) if persona and persona.active_scene_id else None
    return SceneState(
        active=SceneSnapshot.model_validate(active) if active and active.status == SceneStatus.READY.value else None,
        policy=ScenePolicy(persona.scene_policy) if persona else ScenePolicy.LLM_MAY_REPLACE,
        pending=_snapshot(await get_pending_scene(db, user_id)),
        regenerating=_snapshot(await get_regenerating_scene(db, user_id)),
        version=persona.scene_state_version if persona else 0,
        switch_version=persona.scene_switch_version if persona else 0,
    )


async def list_scenes(
    db: AsyncSession,
    user_id: int,
    *,
    query: str = "",
    offset: int = 0,
    limit: int = 30,
    ready_only: bool = False,
) -> tuple[list[CompanionScene], int]:
    conditions = [CompanionScene.user_id == user_id]
    if ready_only:
        conditions.extend(
            [
                CompanionScene.status == SceneStatus.READY.value,
                CompanionScene.title != "",
                CompanionScene.description != "",
                CompanionScene.media_path != "",
            ],
        )
    if needle := query.strip():
        conditions.append(
            or_(
                CompanionScene.title.icontains(needle, autoescape=True),
                CompanionScene.description.icontains(needle, autoescape=True),
            ),
        )
    total = await db.scalar(select(func.count()).select_from(CompanionScene).where(*conditions))
    rows = list(
        (
            await db.scalars(
                select(CompanionScene)
                .where(*conditions)
                .order_by(CompanionScene.id.desc())
                .offset(max(0, offset))
                .limit(min(100, max(1, limit))),
            )
        ).all(),
    )
    return rows, int(total or 0)


def response_for_scene(row: CompanionScene | SceneSnapshot) -> SceneResponse:
    return SceneResponse.model_validate(
        {
            "id": row.id,
            "status": row.status,
            "stage": row.stage,
            "origin": row.origin,
            "source": row.source,
            **{
                key: schema.model_validate_json(value) if value else None
                for key, value, schema in (
                    ("target_size", row.target_size_json, SceneImageSize),
                    ("source_size", row.source_size_json, SceneImageDimensions),
                    ("image_size", row.image_size_json, SceneImageDimensions),
                )
            },
            "title": row.title,
            "description": row.description,
            "requirements": row.requirements,
            "prompt": row.prompt,
            "url": (asset_store.signed_companion_asset_url(row.media_path) or "") if row.media_path else "",
            "error": row.error,
            "auto_activate": row.auto_activate,
            "switch_version": row.switch_version,
            "attempt_count": row.attempt_count,
            "requested_at": row.requested_at,
            "ready_at": row.ready_at,
            "activated_at": row.activated_at,
            "regeneration": (
                {
                    "task_id": row.regeneration_task_id,
                    "status": row.regeneration_status,
                    "stage": row.regeneration_stage or "",
                    "error": row.regeneration_error,
                }
                if row.regeneration_status and row.regeneration_task_id
                else None
            ),
        },
    )


def scene_environment(state: SceneState) -> dict[str, Any]:
    active = state.active
    pending = state.pending
    return {
        "current": {"id": active.id, "title": active.title, "description": active.description} if active else None,
        "pending_switch": {
            "id": pending.id,
            "requirements": pending.requirements,
            "stage": pending.stage,
        }
        if pending and pending.auto_activate and pending.switch_version == state.switch_version
        else None,
        "policy": state.policy,
        "version": state.version,
    }


def _snapshot(row: CompanionScene | None) -> SceneSnapshot | None:
    return SceneSnapshot.model_validate(row) if row is not None else None


async def scene_snapshot(db: AsyncSession, user_id: int, scene_id: int) -> SceneSnapshot | None:
    return _snapshot(await get_scene(db, user_id, scene_id))


async def initial_scene_snapshot(db: AsyncSession, user_id: int) -> SceneSnapshot | None:
    """优先初始化场景；用户先创建场景时等待首条来源就绪，不跟随当前启用项。"""
    row = await db.scalar(
        select(CompanionScene)
        .where(CompanionScene.user_id == user_id)
        .order_by((CompanionScene.origin == SceneOrigin.ONBOARDING.value).desc(), CompanionScene.id)
        .limit(1),
    )
    return _snapshot(row) if row is not None and row.status == SceneStatus.READY.value else None
