"""独立音色方案和克隆参考资产。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_voices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("model_card_id", sa.String(36), nullable=False),
        sa.Column("provider_voice_id", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("reference_path", sa.Text(), nullable=True),
        sa.Column("preview_path", sa.Text(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("expected_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("expected_voice_id", sa.Text(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('sample', 'design', 'clone')", name="voice_kind"),
    )
    op.create_index("ix_custom_voices_user_id", "custom_voices", ["user_id"])
    op.create_index("ix_custom_voices_expires_at", "custom_voices", ["expires_at"])
    op.create_index("uq_custom_voice_token", "custom_voices", ["user_id", "token"], unique=True)
    op.create_index(
        "uq_current_custom_voice",
        "custom_voices",
        ["user_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    op.drop_table("custom_voices")
