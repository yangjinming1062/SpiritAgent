"""持久保存近况媒体计划、制作进度及可读成品。"""

from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from infrastructure.assets.asset_store import client_asset_url
from infrastructure.assets.references import asset_paths
from infrastructure.models import id_in
from runtime.events.emit import emit_ws_event
from sqlalchemy import String, and_, cast, func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from features.accounts.queries import user_is_active
from features.assets.lifecycle import enqueue_asset_cleanup
from features.assets.service import (
    asset_paths_in_session,
    ensure_asset_references_in_session,
    ensure_assets_in_session,
    has_referenced_asset_path,
    referenced_asset_paths,
)

from .models import LifeEpisodeMedia, LifeEvent, LifeState
from .schemas import EpisodeImagePlan, EpisodeMediaKind, EpisodeMediaState, LifeEpisodeMediaView

EPISODE_MEDIA_DIRECTORY = "life-media"
_PENDING = ("queued", "running", "unknown")


@dataclass(frozen=True, slots=True)
class EpisodeOriginalAsset:
    kind: EpisodeMediaKind
    asset_path: str
    poster_path: str | None = None


type EpisodeOriginalResolver = Callable[[AsyncSession, int, dict[str, int]], Awaitable[EpisodeOriginalAsset | None]]


@dataclass(frozen=True, slots=True)
class EpisodeMediaWork:
    id: int
    user_id: int
    event_id: int
    position: int
    state: EpisodeMediaState
    kind: EpisodeMediaKind
    alt: str
    plan: dict[str, Any]
    checkpoint: dict[str, Any]
    job_id: int | None


async def add_generated_episode_media(
    db: AsyncSession,
    user_id: int,
    event_id: int,
    kind: EpisodeMediaKind,
    items: Sequence[EpisodeImagePlan],
    plans: Sequence[dict[str, Any]],
) -> None:
    references: set[str] = set()
    for position, (item, plan) in enumerate(zip(items, plans, strict=True)):
        references.update(asset_paths(plan.get("input_paths"), user_id))
        db.add(
            LifeEpisodeMedia(
                user_id=user_id,
                event_id=event_id,
                position=position,
                kind=kind,
                source="generated",
                state="queued",
                alt=item.alt,
                plan_json=plan,
            )
        )
    await ensure_assets_in_session(db, user_id, references)


async def episode_asset_ids(db: AsyncSession, user_id: int, asset: EpisodeOriginalAsset) -> tuple[int, int | None]:
    paths = {asset.asset_path}
    if asset.poster_path:
        paths.add(asset.poster_path)
    refs = await ensure_asset_references_in_session(db, user_id, paths)
    main = refs[asset.asset_path]
    poster = refs.get(asset.poster_path) if asset.poster_path else None
    if not main.media_type.startswith(f"{asset.kind}/") or (poster and not poster.media_type.startswith("image/")):
        raise ValueError("近况的媒体类型与正式文件不符")
    return main.id, poster.id if poster else None


def add_original_episode_media(
    db: AsyncSession,
    user_id: int,
    event_id: int,
    kind: EpisodeMediaKind,
    alt: str,
    asset_ids: tuple[int, int | None],
) -> None:
    asset_id, poster_asset_id = asset_ids
    db.add(
        LifeEpisodeMedia(
            user_id=user_id,
            event_id=event_id,
            position=0,
            kind=kind,
            source="original",
            state="succeeded",
            alt=alt,
            asset_id=asset_id,
            poster_asset_id=poster_asset_id,
        )
    )


async def link_episode_share_topic(db: AsyncSession, episode: LifeEvent, original: LifeEvent) -> None:
    """将分享连接到同主题的可见经历，同时保留原片段的私有身份。"""
    if episode.topic_id is not None or original.trajectory_kind is None:
        return
    candidate = original if original.shared_with_user else None
    if candidate is None:
        source = aliased(LifeEvent)
        candidate = await db.scalar(
            select(LifeEvent)
            .join(source, LifeEvent.fact_json["event_id"].as_string() == cast(source.id, String))
            .where(
                source.user_id == episode.user_id,
                func.coalesce(source.topic_id, source.id) == (original.topic_id or original.id),
                LifeEvent.user_id == episode.user_id,
                LifeEvent.id != episode.id,
                LifeEvent.source == "life_share",
                LifeEvent.shared_with_user.is_(True),
            )
            .order_by((LifeEvent.occurred_at <= episode.occurred_at).desc(), LifeEvent.occurred_at.desc())
            .limit(1)
        )
    if candidate is not None:
        episode.topic_id = candidate.topic_id or candidate.id
        if (candidate.occurred_at, candidate.id) < (episode.occurred_at, episode.id):
            episode.previous_episode_id = candidate.id


async def _episode_media_row(db: AsyncSession, user_id: int, slot_id: int, *, lock: bool) -> LifeEpisodeMedia | None:
    query = select(LifeEpisodeMedia).where(
        LifeEpisodeMedia.id == slot_id, LifeEpisodeMedia.user_id == user_id, LifeEpisodeMedia.source == "generated"
    )
    return await db.scalar(query.with_for_update() if lock else query)


async def read_episode_media_work(
    db: AsyncSession, user_id: int, slot_id: int, *, lock: bool = False
) -> EpisodeMediaWork | None:
    row = await _episode_media_row(db, user_id, slot_id, lock=lock)
    if row is None:
        return None
    return EpisodeMediaWork(
        row.id,
        row.user_id,
        row.event_id,
        row.position,
        row.state,
        row.kind,
        row.alt,
        row.plan_json,
        row.checkpoint,
        row.job_id,
    )


async def pending_episode_media_work(db: AsyncSession, user_id: int | None = None) -> list[tuple[int, int]]:
    query = select(LifeEpisodeMedia.user_id, LifeEpisodeMedia.id).where(
        LifeEpisodeMedia.source == "generated",
        or_(
            LifeEpisodeMedia.state.in_(("queued", "running")),
            and_(
                LifeEpisodeMedia.state == "unknown",
                LifeEpisodeMedia.kind == "video",
                LifeEpisodeMedia.job_id.is_not(None),
            ),
        ),
    )
    if user_id is not None:
        query = query.where(LifeEpisodeMedia.user_id == user_id)
    return list((await db.execute(query.order_by(LifeEpisodeMedia.updated_at, LifeEpisodeMedia.id))).tuples().all())


async def pending_episode_video_job_ids(db: AsyncSession, user_id: int) -> set[int]:
    jobs = await db.scalars(
        select(LifeEpisodeMedia.job_id).where(
            LifeEpisodeMedia.user_id == user_id,
            LifeEpisodeMedia.source == "generated",
            LifeEpisodeMedia.kind == "video",
            LifeEpisodeMedia.state.in_(_PENDING),
            LifeEpisodeMedia.job_id.is_not(None),
        )
    )
    return {job_id for job_id in jobs if job_id is not None}


async def write_episode_media_progress(
    db: AsyncSession,
    user_id: int,
    slot_id: int,
    *,
    state: Literal["queued", "running", "unknown"] | None = None,
    checkpoint: dict[str, Any] | None = None,
    job_id: int | None = None,
) -> bool:
    row = await _episode_media_row(db, user_id, slot_id, lock=True)
    if row is None or row.state not in _PENDING:
        return False
    if job_id is not None:
        if row.job_id is not None and row.job_id != job_id:
            return False
        row.job_id = job_id
    if state is not None:
        row.state = state
    if checkpoint is not None:
        row.checkpoint = checkpoint
    return True


async def settle_episode_media(
    db: AsyncSession,
    user_id: int,
    slot_id: int,
    *,
    state: Literal["succeeded", "failed", "unknown"] = "succeeded",
    asset_path: str | None = None,
    poster_path: str | None = None,
) -> bool:
    if state == "succeeded":
        # 与分享受理同顺序锁定账户和生活状态，避免资产与通知外键反向等待账户。
        if not await user_is_active(db, user_id, share_lock=True):
            return False
        owner = await db.scalar(select(LifeState.user_id).where(LifeState.user_id == user_id).with_for_update())
        if owner is None:
            return False
    row = await _episode_media_row(db, user_id, slot_id, lock=True)
    if row is None or row.state not in _PENDING:
        return False
    if state == "succeeded":
        if not asset_path:
            raise ValueError("近况媒体成功时需要正式文件")
        episode = await db.scalar(
            select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.id == row.event_id).with_for_update()
        )
        if episode is None:
            return False
        row.asset_id, row.poster_asset_id = await episode_asset_ids(
            db, user_id, EpisodeOriginalAsset(row.kind, asset_path, poster_path)
        )
        row.state = state
        if not episode.shared_with_user and episode.topic_id is None:
            original = await db.scalar(
                select(LifeEvent).where(
                    LifeEvent.user_id == user_id, LifeEvent.id == int(episode.fact_json["event_id"])
                )
            )
            if original is not None:
                await link_episode_share_topic(db, episode, original)
        episode.shared_with_user = True
        emit_ws_event(
            db, user_id=user_id, event_type="companion.life.updated", payload={"event_key": episode.event_key}
        )
    row.state = state
    if state != "unknown":
        paths = asset_paths((row.plan_json.get("input_paths", []), row.checkpoint), user_id)
        await enqueue_asset_cleanup(db, user_id, paths - {asset_path, poster_path})
    return True


async def public_episode_media(
    db: AsyncSession, user_id: int, event_ids: Collection[int]
) -> dict[int, list[LifeEpisodeMediaView]]:
    if not event_ids:
        return {}
    rows = (
        await db.scalars(
            select(LifeEpisodeMedia)
            .where(
                LifeEpisodeMedia.user_id == user_id,
                id_in(LifeEpisodeMedia.event_id, event_ids),
                LifeEpisodeMedia.state == "succeeded",
            )
            .order_by(LifeEpisodeMedia.event_id, LifeEpisodeMedia.position)
        )
    ).all()
    paths = await asset_paths_in_session(
        db, user_id, {asset for row in rows for asset in (row.asset_id, row.poster_asset_id) if asset is not None}
    )
    visible: dict[int, list[LifeEpisodeMediaView]] = {}
    for row in rows:
        if row.asset_id is None or row.asset_id not in paths:
            continue
        poster = paths.get(row.poster_asset_id) if row.poster_asset_id is not None else None
        scope = "?episode_media=true" if row.source == "original" else ""
        visible.setdefault(row.event_id, []).append(
            LifeEpisodeMediaView(
                id=row.id,
                kind=row.kind,
                url=client_asset_url(paths[row.asset_id]) + scope,
                alt=row.alt,
                poster_url=client_asset_url(poster) + scope if poster else None,
            )
        )
    return visible


async def episode_media_file_is_visible(db: AsyncSession, user_id: int, path: str) -> bool:
    visible = (
        select(LifeEpisodeMedia)
        .join(LifeEvent, (LifeEvent.user_id == LifeEpisodeMedia.user_id) & (LifeEvent.id == LifeEpisodeMedia.event_id))
        .where(
            LifeEpisodeMedia.user_id == user_id,
            LifeEpisodeMedia.state == "succeeded",
            LifeEvent.shared_with_user.is_(True),
        )
    )
    return await has_referenced_asset_path(
        db,
        user_id,
        path,
        union(
            visible.with_only_columns(LifeEpisodeMedia.asset_id),
            visible.with_only_columns(LifeEpisodeMedia.poster_asset_id),
        ),
    )


async def live_episode_media_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    owned = select(LifeEpisodeMedia).where(LifeEpisodeMedia.user_id == user_id)
    paths = await referenced_asset_paths(
        db,
        user_id,
        union(
            owned.with_only_columns(LifeEpisodeMedia.asset_id),
            owned.with_only_columns(LifeEpisodeMedia.poster_asset_id),
        ),
    )
    pending = await db.execute(
        owned.with_only_columns(LifeEpisodeMedia.plan_json, LifeEpisodeMedia.checkpoint).where(
            LifeEpisodeMedia.state.in_(_PENDING)
        )
    )
    for plan, checkpoint in pending:
        paths |= asset_paths((plan.get("input_paths", []), checkpoint), user_id)
    return paths
