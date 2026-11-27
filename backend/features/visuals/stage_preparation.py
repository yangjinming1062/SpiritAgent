"""整景准备决定、在途工作与可复用库存。"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from infrastructure.api_ids import ResourceId
from infrastructure.models import ModelBase
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .eligibility import automatic_media_ready, existing_asset
from .geometry import visual_aspect_matches
from .models import VisualItem, VisualJob, VisualPresentation, VisualVersion
from .schemas import ActorOpeningFrame, CinematicSource, VisualMediaKind, VisualSnapshot


class VisualStagePreparation(ModelBase):
    __tablename__ = "visual_stage_preparations"

    id = None
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int]
    input_revision: Mapped[int] = mapped_column(BigInteger)
    basis: Mapped[str] = mapped_column(String(64))
    decision_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class VisualStagePreparationInputs(ModelBase):
    __tablename__ = "visual_stage_preparation_inputs"

    id = None
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger)


class InventoryOpening(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["inventory"]
    visual_id: ResourceId
    version: int = Field(ge=1)


class PlannedOpening(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["planned"]
    item: str = Field(min_length=1, max_length=32)


class PreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    scene_id: ResourceId
    action_id: ResourceId
    media_kind: VisualMediaKind
    requirements: str = Field(min_length=1, max_length=2000)
    submit_at: AwareDatetime | None = None
    opening: Annotated[InventoryOpening | PlannedOpening, Field(discriminator="kind")] | None = None


class PreparationChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=600)
    reuse_visual_ids: list[ResourceId] = Field(default_factory=list)
    items: list[PreparationRequest] = Field(default_factory=list)
    review_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def check_dependencies(self) -> "PreparationChoice":
        earlier: dict[str, PreparationRequest] = {}
        for item in self.items:
            if item.key in earlier:
                raise ValueError("Preparation item keys must be unique")
            if isinstance(item.opening, PlannedOpening):
                source = earlier.get(item.opening.item)
                if source is None or source.scene_id != item.scene_id or source.media_kind != "video":
                    raise ValueError("A planned opening must refer to an earlier video in the same environment")
            if item.opening is not None and item.media_kind != "video":
                raise ValueError("A continuation uses video")
            earlier[item.key] = item
        return self


class PreparationItem(BaseModel):
    request: PreparationRequest
    snapshot: VisualSnapshot
    job_id: ResourceId | None = None
    skipped: bool = False
    reused_opening: InventoryOpening | None = None


class PreparationDecision(BaseModel):
    id: str
    reason: str
    reuse_visual_ids: list[ResourceId] = Field(default_factory=list)
    items: list[PreparationItem] = Field(default_factory=list)
    review_at: AwareDatetime | None = None


@dataclass(frozen=True, slots=True)
class SavedPreparation:
    revision: int
    input_revision: int
    basis: str
    decision: PreparationDecision
    due_at: datetime | None


async def saved_preparation(db: AsyncSession, user_id: int) -> SavedPreparation | None:
    row = await db.get(VisualStagePreparation, user_id)
    if row is None:
        return None
    return SavedPreparation(
        row.revision, row.input_revision, row.basis, PreparationDecision.model_validate(row.decision_json), row.due_at
    )


async def preparation_input_revision(db: AsyncSession, user_id: int, *, lock: bool = False) -> int:
    query = select(VisualStagePreparationInputs.revision).where(VisualStagePreparationInputs.user_id == user_id)
    if lock:
        await db.execute(
            insert(VisualStagePreparationInputs)
            .values(user_id=user_id, revision=0)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        query = query.with_for_update()
    return (await db.scalar(query)) or 0


async def lock_current_preparation(db: AsyncSession, user_id: int, expected: SavedPreparation) -> bool:
    """决定与输入修订按同一账户输入锁串行写入。"""
    if await preparation_input_revision(db, user_id, lock=True) != expected.input_revision:
        return False
    revision = await db.scalar(select(VisualStagePreparation.revision).where(VisualStagePreparation.user_id == user_id))
    return revision == expected.revision


async def save_preparation(
    db: AsyncSession, user_id: int, value: SavedPreparation, *, expected_revision: int | None
) -> bool:
    if await preparation_input_revision(db, user_id, lock=True) != value.input_revision:
        return False
    fields = {
        "revision": value.revision,
        "input_revision": value.input_revision,
        "basis": value.basis,
        "decision_json": value.decision.model_dump(mode="json"),
        "due_at": value.due_at,
    }
    if expected_revision is None:
        query = (
            insert(VisualStagePreparation)
            .values(user_id=user_id, **fields)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
    else:
        query = (
            update(VisualStagePreparation)
            .where(VisualStagePreparation.user_id == user_id, VisualStagePreparation.revision == expected_revision)
            .values(**fields)
        )
    return (await db.scalar(query.returning(VisualStagePreparation.user_id))) is not None


async def due_preparation_users(db: AsyncSession, users: Collection[int], now: datetime) -> set[int]:
    if not users:
        return set()
    deferred = (
        select(VisualStagePreparation.user_id)
        .outerjoin(VisualStagePreparationInputs, VisualStagePreparationInputs.user_id == VisualStagePreparation.user_id)
        .where(
            VisualStagePreparation.input_revision == func.coalesce(VisualStagePreparationInputs.revision, 0),
            or_(VisualStagePreparation.due_at.is_(None), VisualStagePreparation.due_at > now),
        )
    )
    disabled = select(VisualPresentation.user_id).where(VisualPresentation.autonomous_enabled.is_(False))
    blocked = set(await db.scalars(deferred.union(disabled)))
    return set(users) - blocked


async def active_preparation_job(db: AsyncSession, user_id: int) -> bool:
    return (
        await db.scalar(
            select(VisualJob.id)
            .where(
                VisualJob.user_id == user_id,
                VisualJob.checkpoint["source"].astext == "stage_preparation",
                VisualJob.state.in_(("queued", "running")),
            )
            .limit(1)
        )
    ) is not None


async def preparation_result_version(db: AsyncSession, user_id: int, job_id: int) -> int | None:
    return await db.scalar(
        select(VisualVersion.number)
        .select_from(VisualJob)
        .join(VisualVersion, VisualJob.result_version_id == VisualVersion.id)
        .where(VisualJob.user_id == user_id, VisualJob.id == job_id)
    )


@dataclass(frozen=True, slots=True)
class PreparationStock:
    visual_id: int
    version: int
    kind: VisualMediaKind
    description: str
    source: CinematicSource

    def first_frame(self) -> ActorOpeningFrame | None:
        frame = self.source.delivery_first_frame
        if (
            frame is None
            or self.kind != "video"
            or self.source.action.clip_kind != "loop"
            or self.source.media_semantics is None
            or "sleeping" not in self.source.media_semantics.semantic_tags
            or not existing_asset(frame.path)
        ):
            return None
        return ActorOpeningFrame(visual_id=self.visual_id, version=self.version, **frame.model_dump())


async def preparation_stock(
    db: AsyncSession,
    user_id: int,
    *,
    outfit_path: str,
    identity_id: int,
    identity_revision: int,
    target: tuple[int, int],
    visual_id: int | None = None,
    version_number: int | None = None,
) -> list[PreparationStock]:
    source = VisualVersion.snapshot_json["source"]
    conditions = [
        VisualItem.user_id == user_id,
        VisualItem.asset_kind == "cinematic",
        VisualItem.status == "ready",
        source["kind"].astext == "cinematic",
        source["outfit"]["path"].astext == outfit_path,
        source["identity"]["avatar_id"].astext == str(identity_id),
        source["identity"]["revision"].astext == str(identity_revision),
    ]
    if visual_id is not None:
        conditions.append(VisualItem.id == visual_id)
    if version_number is not None:
        conditions.append(VisualVersion.number == version_number)
    else:
        conditions.append(VisualItem.current_version_id == VisualVersion.id)
    rows = await db.stream(
        select(VisualItem.id, VisualItem.media_kind, VisualVersion)
        .join(VisualVersion, VisualItem.id == VisualVersion.visual_id)
        .where(*conditions)
        .order_by(VisualVersion.id.desc()),
        execution_options={"yield_per": 32},
    )
    stock: list[PreparationStock] = []
    seen: set[str] = set()
    async for identifier, media_kind, version in rows:
        if media_kind not in {"image", "video"} or version.content_hash in seen:
            continue
        if not visual_aspect_matches(version.width, version.height, *target):
            continue
        snapshot = VisualSnapshot.model_validate(version.snapshot_json)
        frozen = snapshot.source
        if not isinstance(frozen, CinematicSource):
            continue
        clip = frozen.action.clip_kind if media_kind == "video" else "loop"
        if not automatic_media_ready(
            media_kind=media_kind,
            asset_kind="cinematic",
            media_path=version.media_path,
            auto_selectable=frozen.media_semantics and frozen.media_semantics.auto_selectable,
            clip_kind=clip,
            body=None,
            presentation_approved=frozen.presentation_approved,
            review_result=snapshot.review_result,
            reference_source=version.snapshot_json["source"],
            media_content_hash=version.content_hash,
            scene_reference=frozen.scene.scene_id is not None,
            action_clip_kind=frozen.action.clip_kind,
        ):
            continue
        seen.add(version.content_hash)
        stock.append(PreparationStock(identifier, version.number, media_kind, version.description, frozen))
    await rows.close()
    return stock


async def recent_preparation_work(db: AsyncSession, user_id: int, *, outfit_id: int) -> list[dict[str, Any]]:
    source = VisualJob.snapshot_json["source"]
    rows = await db.execute(
        select(
            VisualJob.target_kind,
            VisualJob.state,
            VisualJob.scene_id,
            VisualJob.action_id,
            source["requirements"].astext.label("requirements"),
        )
        .where(
            VisualJob.user_id == user_id,
            VisualJob.asset_kind == "cinematic",
            VisualJob.outfit_id == outfit_id,
            VisualJob.state != "succeeded",
        )
        .order_by(VisualJob.state.in_(("queued", "running", "paused", "unknown")).desc(), VisualJob.id.desc())
        .limit(32)
    )
    return [
        {
            "media_kind": row.target_kind,
            "state": row.state,
            "scene_id": str(row.scene_id) if row.scene_id is not None else None,
            "action_id": str(row.action_id) if row.action_id is not None else None,
            "requirements": (row.requirements or "")[:350],
        }
        for row in rows
    ]
