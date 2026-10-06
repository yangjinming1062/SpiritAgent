"""动作资产 ORM：pack、action、提案与播放事实。三层身份 outfit_id → pack_id（冻结外观包）→ action_id；系统槽位见 SYSTEM_SLOTS，动态动作不可占用。"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from common import ModelBase, TimestampMixin
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

# 系统动作槽位：产品语义占位，动态动作不可占用或覆盖。
SYSTEM_SLOTS: tuple[str, ...] = ("idle", "drag", "walk_left", "walk_right", "peek_left", "peek_right")
# 发布与激活的必需槽位；缺失时不得 ready。
REQUIRED_SYSTEM_SLOTS: tuple[str, ...] = ("idle", "drag")
SYSTEM_ACTION_MEDIA_TYPES: dict[str, Literal["image", "video"]] = {
    slot: "image" if slot in ("drag", "peek_left", "peek_right") else "video" for slot in SYSTEM_SLOTS
}


def make_action_reference_hash(
    outfit_id: int | None,
    fullbody_path: str,
    avatar_id: int | None,
    reference_uri: str,
) -> str:
    payload = "|".join(
        (
            str(outfit_id) if outfit_id is not None else "",
            fullbody_path,
            str(avatar_id) if avatar_id is not None else "",
            reference_uri,
        ),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CompanionActionPack(ModelBase, TimestampMixin):
    """单外观冻结动作包；动作按外观快照隔离，生成任务只向当前 pack 追加。catalog_version CAS 推进提供不可变目录快照；appearance_epoch 为激活代次（重穿同一包也推进）。"""

    __tablename__ = "companion_action_packs"
    __table_args__ = (
        Index("uq_companion_action_packs_one_active", "user_id", unique=True, postgresql_where=text("active")),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    outfit_id: Mapped[int | None] = mapped_column(ForeignKey("companion_outfits.id"), nullable=True, default=None)
    pack_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    reference_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    reference_hash: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    character_snapshot: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    outfit_snapshot: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    canvas_spec: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    catalog_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    manifest_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    # idle 动作封面，发布为目录 manifest 的 cover_path。
    cover_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    identity_review: Mapped[str] = mapped_column(String(16), default="none", server_default=text("'none'"))
    identity_review_reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(
        String(16),
        default="processing",
        server_default=text("'processing'"),
        index=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    appearance_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CompanionAction(ModelBase, TimestampMixin):
    """单个动作条目（系统槽位或动态动作）；metadata_revision 标识当前素材尝试，已采纳版本保留在独立快照中。"""

    __tablename__ = "companion_actions"
    __table_args__ = (
        UniqueConstraint("pack_id", "key", name="uq_companion_actions_pack_key"),
        CheckConstraint("media_type IN ('image', 'video')", name="ck_companion_actions_media_type"),
        CheckConstraint(
            "media_type = 'video' OR (kind IS NULL AND target_duration_seconds IS NULL "
            "AND actual_duration_ms IS NULL AND frames IS NULL AND loopable IS NULL AND hitmask_fps IS NULL)",
            name="ck_companion_actions_image_parameters",
        ),
        Index(
            "uq_companion_actions_pack_slot",
            "pack_id",
            "system_slot",
            unique=True,
            postgresql_where=text("system_slot <> ''"),
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    pack_id: Mapped[int] = mapped_column(
        ForeignKey("companion_action_packs.id", ondelete="CASCADE"),
        index=True,
    )
    key: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    system_slot: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    media_type: Mapped[str] = mapped_column(String(8), default="video", server_default=text("'video'"))
    kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
    motion_description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    use_when: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    avoid_when: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    metadata_revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))

    status: Mapped[str] = mapped_column(
        String(16),
        default="queued",
        server_default=text("'queued'"),
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(16), default="design", server_default=text("'design'"))
    generation_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pose_generation_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    provider_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    reference_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    outfit_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(2048), nullable=True, default=None)
    pose_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    script_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 已采纳素材独立于当前制作尝试；原位重做和拒绝候选不撤销旧版本的播放资格。
    accepted_asset_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 动态动作的设计规格冻结；系统动作为空。
    source_design_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    media_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    media_hash: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    target_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frames: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loopable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cover_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    hitmask_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    hitmask_grid_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hitmask_grid_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hitmask_fps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peek_geometry_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_rect_json: Mapped[str | None] = mapped_column(Text, nullable=True)


@dataclass(frozen=True)
class RemovedActionVideoTask:
    """已删除动作行遗留的远端视频句柄；删除提交后由生成服务尽力撤销。"""

    action_id: int
    task_id: str
    generation_state_json: str | None
    provider: str


def removed_video_tasks(jobs: Iterable[CompanionAction]) -> list[RemovedActionVideoTask]:
    """删除任务行前摘出仍可能在远端运行的句柄；供应商定位沿生成状态冻结值。"""
    return [
        RemovedActionVideoTask(
            action_id=job.id,
            task_id=job.provider_task_id,
            generation_state_json=job.generation_state_json,
            provider=job.provider,
        )
        for job in jobs
        if job.provider_task_id
    ]


class ActionAssetRetirement(ModelBase):
    """释放的动作资产；宽限期后仍须重新核对引用才可删除。"""

    __tablename__ = "action_asset_retirements"
    __table_args__ = (UniqueConstraint("user_id", "path", name="uq_action_asset_retirements_user_path"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(2048))
    retired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionCreation(ModelBase, TimestampMixin):
    """动态动作制作额度账本；删除包或动作不撤销已使用额度。"""

    __tablename__ = "action_creations"
    __table_args__ = (UniqueConstraint("user_id", "creation_key", name="uq_action_creations_user_key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))
    creation_key: Mapped[str] = mapped_column(String(96))
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), index=True)
    action_id: Mapped[int | None] = mapped_column(
        ForeignKey("companion_actions.id", ondelete="SET NULL"),
        nullable=True,
    )


class ActionProposal(ModelBase, TimestampMixin):
    """动作设计提案：语义指纹去重与近 7 天拒绝创意抑制；制作额度由独立账本管理。"""

    __tablename__ = "action_proposals"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "idempotency_key", name="uq_action_proposals_user_source_key"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    pack_id: Mapped[int] = mapped_column(
        ForeignKey("companion_action_packs.id", ondelete="CASCADE"),
        index=True,
    )
    source: Mapped[str] = mapped_column(String(16), default="autonomous", server_default=text("'autonomous'"))
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    semantic_fingerprint: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"), index=True)
    design_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default=text("'pending'"),
        index=True,
    )
    review_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # approve 时刻；制作额度由独立账本按滚动24小时结算。
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    idempotency_key: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    action_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ActionPlayback(ModelBase, TimestampMixin):
    """播放事实账本：play_id 幂等聚合；仅记录可见播放回执。"""

    __tablename__ = "action_playbacks"
    __table_args__ = (UniqueConstraint("play_id", name="uq_action_playbacks_play_id"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    play_id: Mapped[str] = mapped_column(String(64))
    pack_id: Mapped[int] = mapped_column(
        ForeignKey("companion_action_packs.id", ondelete="CASCADE"),
        index=True,
    )
    action_id: Mapped[int] = mapped_column(
        ForeignKey("companion_actions.id", ondelete="CASCADE"),
        index=True,
    )
    appearance_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    source: Mapped[str] = mapped_column(
        String(16),
        default="chat_expression",
        server_default=text("'chat_expression'"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        default="queued",
        server_default=text("'queued'"),
        index=True,
    )
    visible_duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
