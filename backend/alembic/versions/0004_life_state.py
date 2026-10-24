"""建立生活状态并移除旧呈现固定状态。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "life_states",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("current_activity", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("current_scene_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("mood", sa.Text(), nullable=True),
        sa.Column("active_pursuit_id", sa.Integer(), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("scene_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_life_states_user_id", "life_states", ["user_id"], unique=True)
    op.create_index("ix_life_states_active_pursuit_id", "life_states", ["active_pursuit_id"], unique=False)
    op.create_index("ix_life_states_next_review_at", "life_states", ["next_review_at"], unique=False)

    op.create_table(
        "life_pursuits",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
        sa.Column("title", sa.String(length=160), server_default=sa.text("''"), nullable=False),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("desired_result", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("progress", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("next_step", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_ref_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_life_pursuits_user_idempotency"),
    )
    op.create_index("ix_life_pursuits_user_id", "life_pursuits", ["user_id"], unique=False)
    op.create_index("ix_life_pursuits_status", "life_pursuits", ["status"], unique=False)
    op.create_index("ix_life_pursuits_user_status", "life_pursuits", ["user_id", "status"], unique=False)
    op.create_foreign_key(
        "life_states_active_pursuit_id_fkey",
        "life_states",
        "life_pursuits",
        ["active_pursuit_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "life_steps",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=32), server_default=sa.text("'virtual'"), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("state", sa.String(length=16), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("business_ref_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("result_ref_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pursuit_id"], ["life_pursuits.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_life_steps_user_idempotency"),
    )
    op.create_index("ix_life_steps_user_id", "life_steps", ["user_id"], unique=False)
    op.create_index("ix_life_steps_pursuit_id", "life_steps", ["pursuit_id"], unique=False)
    op.create_index("ix_life_steps_state", "life_steps", ["state"], unique=False)
    op.create_index("ix_life_steps_user_state", "life_steps", ["user_id", "state"], unique=False)

    op.create_table(
        "life_events",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(length=180), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("source", sa.String(length=48), nullable=False),
        sa.Column("subject", sa.String(length=24), server_default=sa.text("'companion'"), nullable=False),
        sa.Column("fact_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("pursuit_id", sa.Integer(), nullable=True),
        sa.Column("step_id", sa.Integer(), nullable=True),
        sa.Column("result_ref_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("is_meaningful", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pursuit_id"], ["life_pursuits.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["step_id"], ["life_steps.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "event_key", name="uq_life_events_user_key"),
    )
    op.create_index("ix_life_events_user_id", "life_events", ["user_id"], unique=False)
    op.create_index("ix_life_events_occurred_at", "life_events", ["occurred_at"], unique=False)
    op.create_index("ix_life_events_pursuit_id", "life_events", ["pursuit_id"], unique=False)
    op.create_index("ix_life_events_step_id", "life_events", ["step_id"], unique=False)
    op.create_index("ix_life_events_user_occurred", "life_events", ["user_id", "occurred_at"], unique=False)
    op.create_index("ix_life_events_user_kind", "life_events", ["user_id", "kind"], unique=False)

    op.execute(
        "INSERT INTO life_states (user_id, mood) "
        "SELECT user_id, current_mood FROM personas "
        "ON CONFLICT (user_id) DO UPDATE SET mood = EXCLUDED.mood",
    )
    op.drop_column("personas", "current_mood")
    op.drop_column("visual_presentations", "pinned")


def downgrade() -> None:
    op.add_column(
        "visual_presentations",
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
    )
    op.add_column("personas", sa.Column("current_mood", sa.Text(), nullable=True))
    op.execute(
        "UPDATE personas SET current_mood = life_states.mood FROM life_states WHERE life_states.user_id = personas.user_id"
    )
    op.drop_constraint("life_states_active_pursuit_id_fkey", "life_states", type_="foreignkey")
    op.drop_index("ix_life_events_user_kind", table_name="life_events")
    op.drop_index("ix_life_events_user_occurred", table_name="life_events")
    op.drop_index("ix_life_events_step_id", table_name="life_events")
    op.drop_index("ix_life_events_pursuit_id", table_name="life_events")
    op.drop_index("ix_life_events_occurred_at", table_name="life_events")
    op.drop_index("ix_life_events_user_id", table_name="life_events")
    op.drop_table("life_events")
    op.drop_index("ix_life_steps_user_state", table_name="life_steps")
    op.drop_index("ix_life_steps_state", table_name="life_steps")
    op.drop_index("ix_life_steps_pursuit_id", table_name="life_steps")
    op.drop_index("ix_life_steps_user_id", table_name="life_steps")
    op.drop_table("life_steps")
    op.drop_index("ix_life_pursuits_user_status", table_name="life_pursuits")
    op.drop_index("ix_life_pursuits_status", table_name="life_pursuits")
    op.drop_index("ix_life_pursuits_user_id", table_name="life_pursuits")
    op.drop_table("life_pursuits")
    op.drop_index("ix_life_states_next_review_at", table_name="life_states")
    op.drop_index("ix_life_states_active_pursuit_id", table_name="life_states")
    op.drop_index("ix_life_states_user_id", table_name="life_states")
    op.drop_table("life_states")
