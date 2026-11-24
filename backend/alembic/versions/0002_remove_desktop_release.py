"""移除停用的桌面版本分发表；降级只恢复结构。"""

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("update_versions")


def downgrade() -> None:
    op.create_table(
        "update_versions",
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("release_notes", sa.Text(), nullable=False),
        sa.Column("exe_filename", sa.String(length=256), nullable=False),
        sa.Column("exe_sha512", sa.String(length=128), nullable=False),
        sa.Column("exe_size", sa.Integer(), nullable=False),
        sa.Column("mac_filename", sa.String(length=256), nullable=True),
        sa.Column("mac_sha512", sa.String(length=128), nullable=True),
        sa.Column("mac_size", sa.Integer(), nullable=True),
        sa.Column("runner_filename", sa.String(length=256), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("update_versions_pkey")),
    )
    op.create_index(op.f("ix_update_versions_is_active"), "update_versions", ["is_active"], unique=False)
    op.create_index(op.f("ix_update_versions_version"), "update_versions", ["version"], unique=True)
