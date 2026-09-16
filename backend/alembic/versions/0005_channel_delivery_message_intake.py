"""渠道待交付队列与 IM 入站消息接收去重。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channel_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("peer_id", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["binding_id"], ["channel_bindings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_channel_deliveries_binding_id"),
        "channel_deliveries",
        ["binding_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_channel_deliveries_status"),
        "channel_deliveries",
        ["status"],
        unique=False,
    )
    op.add_column("messages", sa.Column("queued", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False))
    op.add_column("messages", sa.Column("dedup_key", sa.String(length=64), nullable=True))
    op.add_column("messages", sa.Column("context_order", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_messages_dedup_key", "messages", ["dedup_key"])


def downgrade() -> None:
    op.drop_constraint("uq_messages_dedup_key", "messages", type_="unique")
    op.drop_column("messages", "dedup_key")
    op.drop_column("messages", "context_order")
    op.drop_column("messages", "queued")
    op.drop_index(op.f("ix_channel_deliveries_status"), table_name="channel_deliveries")
    op.drop_index(op.f("ix_channel_deliveries_binding_id"), table_name="channel_deliveries")
    op.drop_table("channel_deliveries")
