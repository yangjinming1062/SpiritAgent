"""持久化陪伴意图与可恢复的等待、认领状态。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companion_intents",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="waiting"),
        sa.Column("not_before_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wake_event", sa.String(32), nullable=True),
        sa.Column("event_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("user_id", "status", "wake_at", "expires_at"):
        op.create_index(f"ix_companion_intents_{column}", "companion_intents", [column])
    # 尚未认领的旧内部事件转成等待意图；客户端消息及已运行的旧回合不重放。
    op.execute("""
        INSERT INTO companion_intents (user_id, intent, source_key, not_before_at, wake_at, expires_at)
        SELECT user_id, payload::jsonb->>'prompt', 'migration:' || id, now(), now(), created_at + interval '1 day'
        FROM ws_events
        WHERE event_type = 'cron.turn.request' AND status = 'PENDING'
          AND created_at > now() - interval '1 day'
          AND jsonb_typeof(payload::jsonb->'prompt') = 'string'
          AND length(btrim(payload::jsonb->>'prompt')) > 0
    """)
    op.execute("DELETE FROM ws_events WHERE event_type = 'cron.turn.request'")


def downgrade() -> None:
    op.execute("DELETE FROM ws_events WHERE event_type = 'companion.turn.request'")
    op.drop_table("companion_intents")
