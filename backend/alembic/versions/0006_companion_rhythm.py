"""保存伙伴的本地作息及交流接续状态。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "companion_rhythms",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("schedule_json", postgresql.JSONB()),
        sa.Column("schedule_basis", sa.String(64)),
        sa.Column("retry_schedule_at", sa.DateTime(timezone=True)),
        sa.Column("adjustment_json", postgresql.JSONB()),
        sa.Column("contact_json", postgresql.JSONB()),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("projection_key", sa.String(240)),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_index("ix_companion_rhythms_next_check_at", "companion_rhythms", ["next_check_at"])


def downgrade() -> None:
    op.drop_table("companion_rhythms")
