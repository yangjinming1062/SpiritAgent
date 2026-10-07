"""远程设备授权与消息提交去重，移除未使用的 IM 结构。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("channel_deliveries", "channel_peers", "channel_bindings"):
        op.drop_table(table)
    op.drop_constraint("uq_messages_dedup_key", "messages", type_="unique")
    for column in ("queued", "channel_peer_id", "discarded", "dedup_key", "context_order"):
        op.drop_column("messages", column)
    op.execute(
        "DELETE FROM system_settings WHERE starts_with(setting_key, 'channels_') OR starts_with(setting_key, 'weixin_')",
    )
    op.create_table(
        "remote_pairings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_remote_pairings_token_hash"),
    )
    op.create_index("ix_remote_pairings_user_id", "remote_pairings", ["user_id"])
    op.create_table(
        "remote_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_remote_sessions_token_hash"),
    )
    op.create_index("ix_remote_sessions_user_id", "remote_sessions", ["user_id"])
    op.create_index("ix_remote_sessions_expires_at", "remote_sessions", ["expires_at"])
    op.create_table(
        "prompt_submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'accepted'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("origin_kind", sa.String(16), nullable=False),
        sa.Column("origin_id", sa.String(64), nullable=True),
        sa.Column("message_ids_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_prompt_submissions_user_request"),
    )
    op.create_index("ix_prompt_submissions_user_id", "prompt_submissions", ["user_id"])
    op.create_index("ix_prompt_submissions_conversation_id", "prompt_submissions", ["conversation_id"])


def downgrade() -> None:
    for table in ("prompt_submissions", "remote_sessions", "remote_pairings"):
        op.drop_table(table)
    op.add_column("messages", sa.Column("queued", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False))
    op.add_column("messages", sa.Column("channel_peer_id", sa.String(128), nullable=True))
    op.add_column("messages", sa.Column("discarded", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False))
    op.add_column("messages", sa.Column("dedup_key", sa.String(64), nullable=True))
    op.add_column("messages", sa.Column("context_order", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_messages_dedup_key", "messages", ["dedup_key"])
    op.create_table(
        "channel_bindings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'disabled'"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("credentials", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("account_ref", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("account_name", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "channel", name="uq_channel_bindings_user_channel"),
        sa.UniqueConstraint("conversation_id", name="uq_channel_bindings_conversation_id"),
    )
    op.create_index(op.f("ix_channel_bindings_user_id"), "channel_bindings", ["user_id"], unique=False)
    op.create_index(op.f("ix_channel_bindings_status"), "channel_bindings", ["status"], unique=False)
    op.create_table(
        "channel_peers",
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("peer_id", sa.String(length=128), nullable=False),
        sa.Column("peer_name", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("authorization_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["binding_id"], ["channel_bindings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", "peer_id", name="uq_channel_peers_binding_peer"),
    )
    op.create_index(op.f("ix_channel_peers_binding_id"), "channel_peers", ["binding_id"], unique=False)
    op.create_index(op.f("ix_channel_peers_status"), "channel_peers", ["status"], unique=False)
    op.create_table(
        "channel_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("peer_id", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["binding_id"], ["channel_bindings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_channel_deliveries_binding_id"), "channel_deliveries", ["binding_id"], unique=False)
    op.create_index(op.f("ix_channel_deliveries_status"), "channel_deliveries", ["status"], unique=False)
