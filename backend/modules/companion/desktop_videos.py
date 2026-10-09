"""桌面生活视频：冻结组合、动作、提案与播放事实。"""

from datetime import datetime

from common import ModelBase, TimestampMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column


class DesktopVideoSet(ModelBase, TimestampMixin):
    __tablename__ = "desktop_video_sets"
    __table_args__ = (UniqueConstraint("user_id", "context_hash", name="uq_desktop_video_sets_context"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int | None] = mapped_column(ForeignKey("avatar_assets.id", ondelete="SET NULL"), nullable=True)
    outfit_id: Mapped[int | None] = mapped_column(
        ForeignKey("companion_outfits.id", ondelete="SET NULL"),
        nullable=True,
    )
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("companion_scenes.id", ondelete="SET NULL"), nullable=True)
    context_hash: Mapped[str] = mapped_column(String(64))
    context_json: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(160), default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(String(24), default="preparing", server_default=text("'preparing'"))


class DesktopVideoAction(ModelBase, TimestampMixin):
    __tablename__ = "desktop_video_actions"
    __table_args__ = (UniqueConstraint("set_id", "key", name="uq_desktop_video_actions_key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("desktop_video_sets.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    preset: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    kind: Mapped[str] = mapped_column(String(8), default="loop", server_default=text("'loop'"))
    duration_seconds: Mapped[int] = mapped_column(Integer, default=10, server_default=text("10"))
    use_when_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    avoid_when_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default=text("'queued'"))
    stage: Mapped[str] = mapped_column(String(24), default="prepare", server_default=text("'prepare'"))
    version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    generation_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_asset_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DesktopVideoState(ModelBase, TimestampMixin):
    __tablename__ = "desktop_video_states"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    current_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("desktop_video_sets.id", ondelete="SET NULL"),
        nullable=True,
    )
    selected_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("desktop_video_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    selected_play_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    loop_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("desktop_video_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    set_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    autonomous_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    preparation_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DesktopVideoProposal(ModelBase, TimestampMixin):
    __tablename__ = "desktop_video_proposals"
    __table_args__ = (UniqueConstraint("set_id", "fingerprint", name="uq_desktop_video_proposals_fingerprint"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("desktop_video_sets.id", ondelete="CASCADE"), index=True)
    action_id: Mapped[int | None] = mapped_column(
        ForeignKey("desktop_video_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    design_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default=text("'pending'"))
    review_reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))


class DesktopVideoPlayback(ModelBase, TimestampMixin):
    __tablename__ = "desktop_video_playbacks"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("desktop_video_sets.id", ondelete="CASCADE"), index=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("desktop_video_actions.id", ondelete="CASCADE"))
    play_id: Mapped[str] = mapped_column(String(36), unique=True)
    set_epoch: Mapped[int] = mapped_column(Integer)
    presentation_revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default=text("'queued'"))
    source: Mapped[str] = mapped_column(String(24))
    client_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
