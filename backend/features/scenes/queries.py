"""场景查询与环境上下文；聊天、工具与夜间读取同一真源。"""

from dataclasses import dataclass
from typing import Any

from infrastructure.assets import asset_store
from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from features.companion.visual_state import read_scene_policy
from features.media.character_images import ImageChainState
from features.scenes.models import CompanionScene, SceneJob, ScenePolicy, SceneSource, SceneStatus
from features.scenes.query_contracts import SceneSnapshot
from features.scenes.schemas import SceneImageDimensions, SceneImageSize, SceneResponse


@dataclass(frozen=True, slots=True)
class SceneState:
    policy: ScenePolicy
    ready: tuple[SceneSnapshot, ...]
    pending: SceneSnapshot | None
    regenerating: SceneSnapshot | None


async def get_scene(db: AsyncSession, user_id: int, scene_id: int) -> CompanionScene | None:
    return await db.scalar(
        select(CompanionScene).where(CompanionScene.id == scene_id, CompanionScene.user_id == user_id),
    )


async def get_pending_scene_task(db: AsyncSession, user_id: int) -> CompanionScene | None:
    return await db.scalar(
        select(CompanionScene)
        .where(
            CompanionScene.user_id == user_id,
            or_(
                CompanionScene.status == SceneStatus.PENDING.value,
                CompanionScene.replacement_job.has(SceneJob.availability == "pending"),
            ),
        )
        .order_by(CompanionScene.id.desc())
        .limit(1),
    )


async def scene_policy(db: AsyncSession, user_id: int) -> ScenePolicy:
    """自主添置政策；未完成资料时按可自主处理。"""

    policy = await read_scene_policy(db, user_id)
    return ScenePolicy(policy) if policy else ScenePolicy.LLM_MAY_REPLACE


def _snapshot_statement(conditions: list[ColumnElement[bool]], *, state_only: bool = False) -> Select:
    initial = aliased(SceneJob, flat=True)
    replacement = aliased(SceneJob, flat=True)
    direct = [
        getattr(CompanionScene, name) for name in SceneSnapshot.model_fields if name in CompanionScene.__table__.c
    ]
    retry = and_(
        CompanionScene.status == "failed",
        CompanionScene.source == "generated",
        CompanionScene.media_path == "",
        CompanionScene.prompt != "",
        CompanionScene.target_size_json.is_not(None),
        func.coalesce(initial.stage, "prepare").not_in(("submitting", "review_failed")),
        func.coalesce(initial.checkpoint["phase"].astext, "") != "submitting",
        func.coalesce(initial.checkpoint["stop_reason"].astext, "") != "result_unknown",
    )
    statement = (
        select(
            *direct,
            func.coalesce(initial.stage, "prepare").label("stage"),
            initial.error.label("error"),
            func.coalesce(initial.attempt_count, 0).label("attempt_count"),
            replacement.availability.label("regeneration_status"),
            replacement.stage.label("regeneration_stage"),
            replacement.error.label("regeneration_error"),
            replacement.token.label("regeneration_task_id"),
            retry.label("can_retry_generation"),
        )
        .select_from(CompanionScene)
        .outerjoin(initial, initial.id == CompanionScene.initial_job_id)
        .outerjoin(
            replacement,
            replacement.id == CompanionScene.replacement_job_id,
        )
        .where(*conditions)
    )
    if state_only:
        statement = statement.where(
            or_(CompanionScene.status.in_(("ready", "pending")), replacement.availability == "pending")
        )
    return statement


async def get_scene_state(db: AsyncSession, user_id: int) -> SceneState:
    policy = await scene_policy(db, user_id)
    rows = (
        await db.execute(
            _snapshot_statement([CompanionScene.user_id == user_id], state_only=True).order_by(CompanionScene.id.desc())
        )
    ).mappings()
    items = [SceneSnapshot.model_validate(dict(row)) for row in rows]
    return SceneState(
        policy=policy,
        ready=tuple(row for row in items if row.status == "ready"),
        pending=next((row for row in items if row.status == "pending"), None),
        regenerating=next((row for row in items if row.regeneration_status == "pending"), None),
    )


async def list_scenes(
    db: AsyncSession,
    user_id: int,
    *,
    query: str = "",
    cursor: int | None = None,
    limit: int = 30,
    ready_only: bool = False,
    include_total: bool = False,
) -> tuple[list[SceneSnapshot], int | None, str | None]:
    conditions = [CompanionScene.user_id == user_id]
    if ready_only:
        conditions.extend(
            [
                CompanionScene.status == "ready",
                CompanionScene.title != "",
                CompanionScene.description != "",
                CompanionScene.media_path != "",
            ]
        )
    if needle := query.strip():
        conditions.append(
            or_(
                CompanionScene.title.icontains(needle, autoescape=True),
                CompanionScene.description.icontains(needle, autoescape=True),
            )
        )
    total = (
        int(await db.scalar(select(func.count()).select_from(CompanionScene).where(*conditions)) or 0)
        if include_total
        else None
    )
    if cursor is not None:
        conditions.append(CompanionScene.id < cursor)
    limit = min(100, max(1, limit))
    rows = list(
        (
            await db.execute(_snapshot_statement(conditions).order_by(CompanionScene.id.desc()).limit(limit + 1))
        ).mappings()
    )
    items = [SceneSnapshot.model_validate(dict(row)) for row in rows[:limit]]
    return items, total, str(items[-1].id) if len(rows) > limit else None


def can_retry_scene_generation(row: CompanionScene | SceneSnapshot) -> bool:
    if isinstance(row, SceneSnapshot):
        return row.can_retry_generation
    if (
        row.initial_job is None
        or row.status != SceneStatus.FAILED.value
        or row.source != SceneSource.GENERATED.value
        or row.media_path
        or not row.prompt
        or not row.target_size_json
        or row.initial_job.stage in {"submitting", "review_failed"}
    ):
        return False
    state = ImageChainState.model_validate(row.initial_job.checkpoint) if row.initial_job.checkpoint else None
    return state is None or (state.phase != "submitting" and state.stop_reason != "result_unknown")


def response_for_scene(row: CompanionScene | SceneSnapshot) -> SceneResponse:
    if isinstance(row, CompanionScene):
        row = scene_view(row)
    return SceneResponse.model_validate(
        {
            "id": row.id,
            "status": row.status,
            "stage": row.stage,
            "can_retry_generation": can_retry_scene_generation(row),
            "origin": row.origin,
            "source": row.source,
            **{
                key: schema.model_validate(value) if value else None
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
            "attempt_count": row.attempt_count,
            "requested_at": row.requested_at,
            "ready_at": row.ready_at,
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
    """纯素材列表：policy 与素材状态，不表示当前所在环境。"""
    materials = {row.id: row for row in (*state.ready, state.pending, state.regenerating) if row is not None}
    return {
        "policy": state.policy.value,
        "materials": [
            {"id": row.id, "title": row.title, "description": row.description, "status": row.status}
            for row in materials.values()
        ],
        "generation_pending": state.pending is not None or state.regenerating is not None,
    }


def scene_view(row: CompanionScene) -> SceneSnapshot:
    initial, replacement = row.initial_job, row.replacement_job
    return SceneSnapshot.model_validate(
        {
            **{name: getattr(row, name) for name in SceneSnapshot.model_fields if name in CompanionScene.__table__.c},
            "stage": initial.stage if initial is not None else "prepare",
            "error": initial.error if initial is not None else None,
            "attempt_count": initial.attempt_count if initial is not None else 0,
            "regeneration_status": replacement.availability if replacement is not None else None,
            "regeneration_stage": replacement.stage if replacement is not None else None,
            "regeneration_error": replacement.error if replacement is not None else None,
            "regeneration_task_id": replacement.token if replacement is not None else None,
            "can_retry_generation": can_retry_scene_generation(row),
        }
    )


async def scene_snapshot(db: AsyncSession, user_id: int, scene_id: int) -> SceneSnapshot | None:
    row = await get_scene(db, user_id, scene_id)
    return scene_view(row) if row is not None else None
