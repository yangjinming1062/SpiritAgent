"""共享定义与持久调度意图；素材、供应商任务和播放仍由目标模块拥有。"""

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column


class ActionDefinition(ModelBase, TimestampMixin):
    __tablename__ = "action_definitions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "outfit_id",
            "source_visual_key",
            "normalized_name",
            name="uq_action_definition_name",
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    outfit_id: Mapped[int | None] = mapped_column(
        ForeignKey("companion_outfits.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_visual_key: Mapped[str] = mapped_column(String(64))
    normalized_name: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(64))
    motion_description: Mapped[str] = mapped_column(Text)
    use_when_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    avoid_when_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    clip_kind: Mapped[str] = mapped_column(String(8))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    source: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default=text("'pending'"), index=True)
    review_reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    review_source_json: Mapped[str] = mapped_column(Text)
    origin_target_id: Mapped[int] = mapped_column(Integer)
    reuse_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    fanout_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))


class ActionSyncTarget(ModelBase, TimestampMixin):
    __tablename__ = "action_sync_targets"
    __table_args__ = (UniqueConstraint("definition_id", "target_id", name="uq_action_sync_target"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    definition_id: Mapped[int] = mapped_column(ForeignKey("action_definitions.id", ondelete="CASCADE"), index=True)
    # 目标 ID 属于桌面生活套装，跨模块只经公开操作校验与读取。
    target_id: Mapped[int] = mapped_column(Integer)
    action_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    state: Mapped[str] = mapped_column(String(16), default="manual", server_default=text("'manual'"), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
