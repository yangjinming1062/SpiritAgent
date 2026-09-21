"""场景查询与环境上下文；聊天、工具与夜间读取同一真源。"""

from dataclasses import dataclass
from typing import Any

from modules.companion import CompanionScene, Persona, ScenePolicy, SceneStatus
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import asset_store


@dataclass(frozen=True, slots=True)
class SceneState:
    active: CompanionScene | None
    policy: ScenePolicy
    pending: CompanionScene | None
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


async def get_scene_state(db: AsyncSession, user_id: int) -> SceneState:
    persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
    active = await get_scene(db, user_id, persona.active_scene_id) if persona and persona.active_scene_id else None
    return SceneState(
        active=active if active and active.status == SceneStatus.READY.value else None,
        policy=ScenePolicy(persona.scene_policy) if persona else ScenePolicy.LLM_MAY_REPLACE,
        pending=await get_pending_scene(db, user_id),
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
    if query.strip():
        needle = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(
            or_(CompanionScene.title.ilike(needle, escape="\\"), CompanionScene.description.ilike(needle, escape="\\")),
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


def response_for_scene(row: CompanionScene) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "stage": row.stage,
        "origin": row.origin,
        "source": row.source,
        "title": row.title,
        "description": row.description,
        "requirements": row.requirements,
        "outfit_description": row.outfit_description,
        "prompt": row.prompt,
        "url": (asset_store.signed_companion_asset_url(row.media_path) or "") if row.media_path else "",
        "seed_portrait_media_id": row.seed_portrait_media_id,
        "error": row.error,
        "auto_activate": row.auto_activate,
        "switch_version": row.switch_version,
        "attempt_count": row.attempt_count,
        "requested_at": row.requested_at,
        "ready_at": row.ready_at,
        "activated_at": row.activated_at,
    }


def scene_environment(state: SceneState) -> dict[str, Any]:
    active = state.active
    pending = state.pending
    return {
        "current": {"id": active.id, "title": active.title, "description": active.description} if active else None,
        "pending_switch": {
            "id": pending.id,
            "requirements": pending.requirements,
            "outfit_description": pending.outfit_description,
            "stage": pending.stage,
        }
        if pending and pending.auto_activate and pending.switch_version == state.switch_version
        else None,
        "policy": state.policy,
        "version": state.version,
    }
