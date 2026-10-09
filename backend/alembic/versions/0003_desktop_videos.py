"""桌面生活视频资产、场景上传源文件；移除无消费方的显示尺寸快照表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column(
        "companion_scenes",
        sa.Column("upload_source_path", sa.String(2048), server_default=sa.text("''"), nullable=False),
    )
    op.drop_table("scene_display_targets")
    op.create_table(
        "desktop_video_sets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=True),
        sa.Column("outfit_id", sa.Integer(), nullable=True),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("title", sa.String(160), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'preparing'"), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["avatar_id"], ["avatar_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["outfit_id"], ["companion_outfits.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scene_id"], ["companion_scenes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "context_hash", name="uq_desktop_video_sets_context"),
    )
    op.create_index("ix_desktop_video_sets_user_id", "desktop_video_sets", ["user_id"])
    op.create_table(
        "desktop_video_actions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("preset", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("kind", sa.String(8), server_default=sa.text("'loop'"), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("use_when_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("avoid_when_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("stage", sa.String(24), server_default=sa.text("'prepare'"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("generation_state_json", sa.Text(), nullable=True),
        sa.Column("accepted_asset_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["set_id"], ["desktop_video_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("set_id", "key", name="uq_desktop_video_actions_key"),
    )
    op.create_index("ix_desktop_video_actions_user_id", "desktop_video_actions", ["user_id"])
    op.create_index("ix_desktop_video_actions_set_id", "desktop_video_actions", ["set_id"])
    op.create_table(
        "desktop_video_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("current_set_id", sa.Integer(), nullable=True),
        sa.Column("selected_action_id", sa.Integer(), nullable=True),
        sa.Column("selected_play_id", sa.String(36), nullable=True),
        sa.Column("loop_action_id", sa.Integer(), nullable=True),
        sa.Column("set_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("autonomous_enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("preparation_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_set_id"], ["desktop_video_sets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["selected_action_id"], ["desktop_video_actions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["loop_action_id"], ["desktop_video_actions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_desktop_video_states_user_id", "desktop_video_states", ["user_id"], unique=True)
    op.create_table(
        "desktop_video_proposals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("design_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("review_reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["set_id"], ["desktop_video_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_id"], ["desktop_video_actions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("set_id", "fingerprint", name="uq_desktop_video_proposals_fingerprint"),
    )
    op.create_index("ix_desktop_video_proposals_user_id", "desktop_video_proposals", ["user_id"])
    op.create_index("ix_desktop_video_proposals_set_id", "desktop_video_proposals", ["set_id"])
    op.create_table(
        "desktop_video_playbacks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.Integer(), nullable=False),
        sa.Column("play_id", sa.String(36), nullable=False),
        sa.Column("set_epoch", sa.Integer(), nullable=False),
        sa.Column("presentation_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("client_id", sa.String(80), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["set_id"], ["desktop_video_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_id"], ["desktop_video_actions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("play_id"),
    )
    op.create_index("ix_desktop_video_playbacks_user_id", "desktop_video_playbacks", ["user_id"])
    op.create_index("ix_desktop_video_playbacks_set_id", "desktop_video_playbacks", ["set_id"])


def downgrade() -> None:
    op.create_table(
        "scene_display_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scene_display_targets_user_id", "scene_display_targets", ["user_id"], unique=True)
    for table in (
        "desktop_video_playbacks",
        "desktop_video_proposals",
        "desktop_video_states",
        "desktop_video_actions",
        "desktop_video_sets",
    ):
        op.drop_table(table)
    op.drop_column("companion_scenes", "upload_source_path")
