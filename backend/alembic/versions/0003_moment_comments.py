"""片刻转为精灵主导的朋友圈：新增评论区表；旧系统/手动写入通道停用。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companion_moment_comments",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("moment_id", UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="user", nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["moment_id"], ["companion_moments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_companion_moment_comments_moment_id"),
        "companion_moment_comments",
        ["moment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_companion_moment_comments_user_id"),
        "companion_moment_comments",
        ["user_id"],
        unique=False,
    )
    # 片刻改由精灵主导后，新增行不再出现 greeting/system 缺省；列默认值与模型对齐（存量行值不变）。
    op.alter_column(
        "companion_moments",
        "kind",
        existing_type=sa.String(length=16),
        server_default=sa.text("'emotion'"),
    )
    op.alter_column(
        "companion_moments",
        "source",
        existing_type=sa.String(length=16),
        server_default=sa.text("'nightly'"),
    )


def downgrade() -> None:
    op.alter_column(
        "companion_moments",
        "source",
        existing_type=sa.String(length=16),
        server_default=sa.text("'system'"),
    )
    op.alter_column(
        "companion_moments",
        "kind",
        existing_type=sa.String(length=16),
        server_default=sa.text("'greeting'"),
    )
    op.drop_index(op.f("ix_companion_moment_comments_user_id"), table_name="companion_moment_comments")
    op.drop_index(op.f("ix_companion_moment_comments_moment_id"), table_name="companion_moment_comments")
    op.drop_table("companion_moment_comments")
