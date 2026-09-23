"""动作资产 ORM：pack、action、提案与播放事实。

三层身份：outfit_id → pack_id（冻结外观包）→ action_id。
系统槽位是产品语义（idle/drag/walk_left/walk_right），动态动作不可占用。
"""

from datetime import datetime

from common import ModelBase, TimestampMixin
from sqlalchemy import (
    Boolean,
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
SYSTEM_SLOTS: tuple[str, ...] = ("idle", "drag", "walk_left", "walk_right")
# 发布与激活的必需槽位：首包生成集合；缺失时不得 ready。
REQUIRED_SYSTEM_SLOTS: tuple[str, ...] = ("idle", "drag")


class CompanionActionPack(ModelBase, TimestampMixin):
    """单外观冻结动作包。

    动作按外观快照隔离；生成任务只向当前 pack 追加动作。
    catalog_version CAS 推进，提供目录不可变快照。
    """

    __tablename__ = "companion_action_packs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    outfit_id: Mapped[int | None] = mapped_column(ForeignKey("companion_outfits.id"), nullable=True, default=None)
    pack_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    visual_revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    reference_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    reference_hash: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    character_snapshot: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    outfit_snapshot: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    canvas_spec: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    catalog_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    manifest_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CompanionAction(ModelBase, TimestampMixin):
    """单个动作条目：系统槽位或动态动作。

    内聚元数据、生成状态与生效视频素材参数。
    metadata_revision 在名称/用途等元信息变更时递增，不重新生成视频。
    """

    __tablename__ = "companion_actions"
    __table_args__ = (
        UniqueConstraint("pack_id", "key", name="uq_companion_actions_pack_key"),
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
    name: Mapped[str] = mapped_column(String(64), default="")
    system_slot: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    kind: Mapped[str] = mapped_column(String(8), default="once", server_default=text("'once'"))
    motion_description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    use_when: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    avoid_when: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    tags: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    metadata_revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))

    # 生成状态与阶段（制作中或就绪）
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
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    outfit_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    submitted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    retry_safe: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(2048), nullable=True, default=None)
    pose_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    result_path: Mapped[str | None] = mapped_column(String(2048), nullable=True, default=None)
    script_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 动态动作的提案设计规格冻结；系统动作为空。
    source_design_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 生效素材参数
    video_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    video_hash: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    target_duration_seconds: Mapped[float] = mapped_column(Float, default=2.0, server_default=text("2.0"))
    actual_duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    frames: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    loopable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    cover_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    hitmask_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    hitmask_grid_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hitmask_grid_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hitmask_fps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enter_pose: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_pose: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ActionProposal(ModelBase, TimestampMixin):
    """动作设计提案：语义指纹去重，评审结论与理由独立保存。

    同时作为每日设计额度与近 7 天拒绝创意抑制的权威记录源，无需独立账本表。
    """

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
    source_message_ids: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    semantic_fingerprint: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"), index=True)
    design_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    candidate_action_ids: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default=text("'pending'"),
        index=True,
    )
    review_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 评审 approve 时刻：制作额度按批准日（用户本地日）结算，与受理日区分。
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
    target_device: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    target_surface: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    source: Mapped[str] = mapped_column(
        String(16),
        default="chat_expression",
        server_default=text("'chat_expression'"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        String(16),
        default="queued",
        server_default=text("'queued'"),
        index=True,
    )
    visible_duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
