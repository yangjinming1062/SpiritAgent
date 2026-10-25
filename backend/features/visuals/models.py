"""影像库：组合成品、制作工作项与呈现播放事实。

影像成品只收录专门用于伙伴呈现的组合成品（图片或视频）；聊天配图、动态媒体和素材参考图不进入本模块。
组合快照冻结伙伴身份与衣柜、环境、动作三份素材的引用及描述，素材删除后快照与文件引用继续有效。
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from infrastructure.job_models import JobRecord
from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship


class VisualItem(ModelBase, TimestampMixin):
    """稳定的影像身份和查询摘要；文件及冻结输入归不可变版本。"""

    __tablename__ = "visual_items"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_visual_items_user_id"),
        UniqueConstraint("user_id", "source_key", name="uq_visual_items_user_source"),
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["visual_versions.visual_id", "visual_versions.id"],
            name="fk_visual_item_current_version",
            ondelete="SET NULL (current_version_id)",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        Index(
            "uq_visual_items_image_content",
            "user_id",
            "content_hash",
            unique=True,
            postgresql_where=text("media_kind = 'image'"),
        ),
        Index("ix_visual_items_catalog", "user_id", "status", "media_kind", "id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_key: Mapped[str] = mapped_column(String(36), default=lambda: uuid4().hex)
    media_kind: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(160), default="", server_default="")
    status: Mapped[str] = mapped_column(String(24), default="ready", server_default="ready")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    clip_kind: Mapped[str] = mapped_column(String(8), default="loop", server_default="loop")
    allowed_triggers: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    outfit_id: Mapped[int | None] = mapped_column(ForeignKey("companion_outfits.id", ondelete="SET NULL"), index=True)
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("companion_scenes.id", ondelete="SET NULL"), index=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("action_definitions.id", ondelete="SET NULL"), index=True)
    current_version_id: Mapped[int | None] = mapped_column(BigInteger)
    current_version: Mapped["VisualVersion | None"] = relationship(
        foreign_keys=[current_version_id],
        primaryjoin="VisualItem.current_version_id == VisualVersion.id",
        lazy="joined",
        post_update=True,
    )

    @property
    def version(self) -> int:
        return self.current_version.number if self.current_version is not None else 0

    @property
    def media_path(self) -> str:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.media_path

    @property
    def poster_path(self) -> str | None:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.poster_path

    @property
    def width(self) -> int:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.width

    @property
    def height(self) -> int:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.height

    @property
    def duration_ms(self) -> int | None:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.duration_ms

    @property
    def outfit_description(self) -> str:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.outfit_description

    @property
    def snapshot_json(self) -> dict[str, Any]:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.snapshot_json

    @property
    def input_image_id(self) -> int | None:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.input_image_id

    @property
    def input_image_version_id(self) -> int | None:
        return self.current_version.input_image_version_id if self.current_version is not None else None

    @property
    def input_image_path(self) -> str | None:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.input_image_path

    @property
    def input_image_hash(self) -> str | None:
        if self.current_version is None:
            raise ValueError("影像尚未发布版本")
        return self.current_version.input_image_hash


class VisualVersion(ModelBase):
    """已发布版本只追加；当前呈现和播放账本直接保留版本引用。"""

    __tablename__ = "visual_versions"
    __table_args__ = (
        UniqueConstraint("visual_id", "number", name="uq_visual_versions_number"),
        UniqueConstraint("visual_id", "id", name="uq_visual_versions_item_id"),
        ForeignKeyConstraint(
            ["input_image_id", "input_image_version_id"],
            ["visual_versions.visual_id", "visual_versions.id"],
            name="fk_visual_version_input_pair",
            match="FULL",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("user_id", "id", name="uq_visual_versions_user_id"),
        CheckConstraint("number > 0 AND width > 0 AND height > 0", name="visual_version_dimensions"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    visual_id: Mapped[int] = mapped_column(ForeignKey("visual_items.id", ondelete="CASCADE"), index=True)
    visual: Mapped[VisualItem] = relationship(
        foreign_keys=[visual_id], primaryjoin="VisualVersion.visual_id == VisualItem.id"
    )
    number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    outfit_id: Mapped[int | None] = mapped_column(ForeignKey("companion_outfits.id", ondelete="SET NULL"))
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("companion_scenes.id", ondelete="SET NULL"))
    action_id: Mapped[int | None] = mapped_column(ForeignKey("action_definitions.id", ondelete="SET NULL"))
    media_path: Mapped[str] = mapped_column(Text)
    poster_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outfit_description: Mapped[str] = mapped_column(Text, default="", server_default="")
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_image_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED")
    )
    input_image_version_id: Mapped[int | None] = mapped_column(BigInteger)
    input_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_image_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class VisualVersionAsset(ModelBase):
    __tablename__ = "visual_version_assets"
    __table_args__ = (UniqueConstraint("version_id", "role", "asset_id", name="uq_visual_version_asset_role"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version_id: Mapped[int] = mapped_column(ForeignKey("visual_versions.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    role: Mapped[str] = mapped_column(String(32))


class VisualJob(JobRecord):
    """制作工作项：新建或重做一份影像，与当前成品分离。"""

    __tablename__ = "visual_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["result_visual_id", "result_version_id"],
            ["visual_versions.visual_id", "visual_versions.id"],
            name="fk_visual_job_result_pair",
            match="FULL",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["result_image_id", "result_image_version_id"],
            ["visual_versions.visual_id", "visual_versions.id"],
            name="fk_visual_job_image_pair",
            match="FULL",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    __mapper_args__ = {"polymorphic_identity": "visual.generate"}

    id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    outfit_id: Mapped[int | None] = mapped_column(ForeignKey("companion_outfits.id", ondelete="SET NULL"))
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("companion_scenes.id", ondelete="SET NULL"))
    action_id: Mapped[int | None] = mapped_column(ForeignKey("action_definitions.id", ondelete="SET NULL"))

    # 重做目标；空表示新建。等待、失败、暂停和取消均保留目标成品。
    target_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String(16), default="create", server_default=text("'create'"))
    target_kind: Mapped[str] = mapped_column(String(8), default="video", server_default=text("'video'"))
    expected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 冻结输入：组合快照（身份、三份素材、计划、目标画幅与生成要求）。
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    feedback: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    video_feedback: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # 供应商进度与制作阶段状态；格式由 visuals.schemas.VisualJobProgress 定义。
    # 可核验候选；未消费的供应商结果不得恢复为活动提交。
    candidates_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    repair_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    repair_limit: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    result_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    # 目标包含视频时，合格起始图也独立入库。
    result_image_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    result_version_id: Mapped[int | None] = mapped_column(BigInteger)
    result_image_version_id: Mapped[int | None] = mapped_column(BigInteger)


class VisualPresentation(ModelBase, TimestampMixin):
    """每账户一份呈现状态：当前影像、持续影像与播放代次。"""

    __tablename__ = "visual_presentations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["current_visual_id", "current_version_id"],
            ["visual_versions.visual_id", "visual_versions.id"],
            name="fk_presentation_current_pair",
            match="FULL",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["loop_visual_id", "loop_version_id"],
            ["visual_versions.visual_id", "visual_versions.id"],
            name="fk_presentation_loop_pair",
            match="FULL",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id = None
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    current_version_id: Mapped[int | None] = mapped_column(BigInteger)
    loop_version_id: Mapped[int | None] = mapped_column(BigInteger)
    current_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    # 短动作播放期间保留的有效持续影像。
    loop_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    autonomous_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    # 本机呈现上下文的目标画幅（像素），组合制作以此冻结目标。
    target_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    presentation_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    state_revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # 待执行播放指令；换号、模式切换和过期后重新校验。
    pending_command_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    current_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    loop_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)


class VisualPlayback(ModelBase, TimestampMixin):
    """播放账本：指令受理、桌面认领与实际回执。"""

    __tablename__ = "visual_playbacks"
    __table_args__ = (
        UniqueConstraint("user_id", "play_id", name="uq_visual_playbacks_user_play"),
        ForeignKeyConstraint(
            ["visual_id", "version_id"],
            ["visual_versions.visual_id", "visual_versions.id"],
            name="fk_playback_version_pair",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    visual_id: Mapped[int] = mapped_column(
        ForeignKey("visual_items.id", ondelete="CASCADE"),
        index=True,
    )
    version_id: Mapped[int] = mapped_column(BigInteger, index=True)
    visual_version: Mapped[int] = mapped_column(Integer)
    play_id: Mapped[str] = mapped_column(String(36))
    presentation_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    state_revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    source: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default=text("'queued'"))
    client_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 实际回执类型：完整图片、视频或窗口静态投影；静态投影不冒充动作执行。
    receipt_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
