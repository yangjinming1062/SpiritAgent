"""为生活事件保存可追溯的近况轨迹。"""

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("life_states", sa.Column("review_slot", sa.String(16)))
    op.add_column("life_events", sa.Column("trajectory_kind", sa.String(16)))
    op.add_column("life_events", sa.Column("trajectory_result_key", sa.String(96)))
    op.add_column("life_events", sa.Column("topic_id", sa.BigInteger()))
    op.add_column("life_events", sa.Column("previous_episode_id", sa.BigInteger()))
    op.create_check_constraint(
        op.f("ck_life_events_trajectory_kind"), "life_events", "trajectory_kind IN ('virtual', 'milestone', 'result')"
    )
    for column, name in (
        ("topic_id", "fk_life_event_topic_owner"),
        ("previous_episode_id", "fk_life_event_previous_episode_owner"),
    ):
        op.create_foreign_key(
            name,
            "life_events",
            "life_events",
            ["user_id", column],
            ["user_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )
    op.create_index(
        "uq_life_events_trajectory_result",
        "life_events",
        ["user_id", "trajectory_result_key", "pursuit_id"],
        unique=True,
        postgresql_where=sa.text("trajectory_result_key IS NOT NULL"),
    )
    op.create_index(
        "ix_life_events_trajectory",
        "life_events",
        ["user_id", sa.text("occurred_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("trajectory_kind IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_life_events_trajectory", table_name="life_events")
    op.drop_index("uq_life_events_trajectory_result", table_name="life_events")
    op.drop_constraint("fk_life_event_previous_episode_owner", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_event_topic_owner", "life_events", type_="foreignkey")
    op.drop_constraint(op.f("ck_life_events_trajectory_kind"), "life_events", type_="check")
    for column in ("previous_episode_id", "topic_id", "trajectory_result_key", "trajectory_kind"):
        op.drop_column("life_events", column)
    op.drop_column("life_states", "review_slot")
