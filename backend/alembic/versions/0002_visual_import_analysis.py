"""用户影像的持久分析、素材匹配与整理进度。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "visual_import_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("visual_id", sa.BigInteger(), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=False),
        sa.Column("context_json", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("result_json", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("selections_json", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.PrimaryKeyConstraint("id", name="visual_import_jobs_pkey"),
        sa.UniqueConstraint("owner_id", "visual_id", name="uq_visual_import_jobs_visual"),
        sa.UniqueConstraint("owner_id", "original_hash", name="uq_visual_import_jobs_hash"),
        sa.UniqueConstraint("owner_id", "id", name="uq_visual_import_jobs_owner_id"),
        sa.ForeignKeyConstraint(
            ["owner_id", "id"], ["jobs.user_id", "jobs.id"], name="fk_visual_import_jobs_id_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "visual_id"],
            ["visual_items.user_id", "visual_items.id"],
            name="fk_visual_import_jobs_visual_id_owner",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_visual_import_jobs_visual_id", "visual_import_jobs", ["visual_id"])


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'visual.import'")
    op.drop_table("visual_import_jobs")
