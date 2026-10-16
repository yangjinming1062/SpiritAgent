"""视频供应商凭据队列与独立远端尝试。"""

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
    op.create_table(
        "video_queue_lanes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(32), nullable=False, unique=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("credential_fingerprint", sa.String(64), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("provider", "credential_fingerprint", name="uq_video_queue_credential"),
    )
    op.create_table(
        "video_queue_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(32), nullable=False, unique=True),
        sa.Column("lane_id", sa.Integer(), sa.ForeignKey("video_queue_lanes.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("generation_id", sa.String(64), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("frozen_json", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_task_id", sa.String(128), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_reason", sa.String(64), nullable=True),
        sa.Column("error_status_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("rate_limit_count", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(128), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("generation_id", "attempt_index", name="uq_video_queue_attempt"),
    )
    op.create_index("ix_video_queue_fifo", "video_queue_requests", ["lane_id", "state", "enqueued_at", "id"])
    op.create_index("ix_video_queue_owner", "video_queue_requests", ["kind", "entity_id", "generation_id"])
    op.create_index("ix_video_queue_requests_user_id", "video_queue_requests", ["user_id"])
    op.create_index("ix_video_queue_requests_state", "video_queue_requests", ["state"])


def downgrade() -> None:
    op.drop_table("video_queue_requests")
    op.drop_table("video_queue_lanes")
