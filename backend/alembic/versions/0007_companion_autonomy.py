"""Persist private life, growth and comment decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str = "0006"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    for table in ("life_events", "life_pursuits"):
        op.add_column(table, sa.Column("shared_with_user", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column(
        "life_states", sa.Column("activity_shared_with_user", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column(
        "life_states",
        sa.Column("personal_growth_json", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.drop_column("personas", "onboarding_progress_json")
    op.add_column("entry_comments", sa.Column("reply_not_before", sa.DateTime(timezone=True)))
    op.add_column("entry_comments", sa.Column("reply_followup", sa.Text()))
    op.drop_constraint(op.f("ck_entry_comments_entry_comment_state"), "entry_comments", type_="check")
    op.create_check_constraint(
        op.f("ck_entry_comments_entry_comment_state"),
        "entry_comments",
        "reply_status IN ('none', 'pending', 'running', 'deferred', 'completed', 'silent', 'failed')",
    )
    op.drop_index("ix_entry_comments_pending", table_name="entry_comments")
    op.create_index(
        "ix_entry_comments_pending",
        "entry_comments",
        ["user_id", "post_id"],
        postgresql_where=sa.text("role = 'user' AND reply_status IN ('pending', 'running', 'deferred')"),
    )


def downgrade() -> None:
    op.drop_index("ix_entry_comments_pending", table_name="entry_comments")
    op.create_index(
        "ix_entry_comments_pending",
        "entry_comments",
        ["user_id", "post_id"],
        postgresql_where=sa.text("role = 'user' AND reply_status IN ('pending', 'running')"),
    )
    op.drop_constraint(op.f("ck_entry_comments_entry_comment_state"), "entry_comments", type_="check")
    op.execute("UPDATE entry_comments SET reply_status = 'pending' WHERE reply_status = 'deferred'")
    op.execute("UPDATE entry_comments SET reply_status = 'completed' WHERE reply_status = 'silent'")
    op.create_check_constraint(
        op.f("ck_entry_comments_entry_comment_state"),
        "entry_comments",
        "reply_status IN ('none', 'pending', 'running', 'completed', 'failed')",
    )
    op.drop_column("entry_comments", "reply_followup")
    op.drop_column("entry_comments", "reply_not_before")
    op.add_column(
        "personas",
        sa.Column("onboarding_progress_json", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.drop_column("life_states", "personal_growth_json")
    op.drop_column("life_states", "activity_shared_with_user")
    op.drop_column("life_pursuits", "shared_with_user")
    op.drop_column("life_events", "shared_with_user")
