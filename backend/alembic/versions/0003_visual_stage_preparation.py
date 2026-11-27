"""保存完整影像准备决定与实际本地呈现。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str = "0002"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "visual_stage_preparations",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("input_revision", sa.BigInteger(), nullable=False),
        sa.Column("basis", sa.String(length=64), nullable=False),
        sa.Column("decision_json", postgresql.JSONB(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("visual_stage_preparations_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("visual_stage_preparations_pkey")),
    )
    op.create_index(op.f("ix_visual_stage_preparations_due_at"), "visual_stage_preparations", ["due_at"], unique=False)
    op.create_table(
        "visual_stage_preparation_inputs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("visual_stage_preparation_inputs_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("visual_stage_preparation_inputs_pkey")),
    )
    op.execute(
        """
        CREATE FUNCTION revise_visual_stage_preparation_inputs() RETURNS trigger AS $$
        BEGIN
            INSERT INTO visual_stage_preparation_inputs (user_id, revision) VALUES (NEW.user_id, 1)
            ON CONFLICT (user_id) DO UPDATE SET revision = visual_stage_preparation_inputs.revision + 1;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER visual_stage_preparation_inputs_changed
        AFTER INSERT ON ws_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (NEW.library_work_id IS NULL AND NEW.event_type IN (
            'companion.visual.preparation_requested', 'companion.life.presentation_requested',
            'companion.visual.initial_requested'
        ))
        EXECUTE FUNCTION revise_visual_stage_preparation_inputs()
        """
    )
    op.add_column(
        "visual_presentations", sa.Column("local_snapshot_json", postgresql.JSONB(none_as_null=True), nullable=True)
    )
    op.execute("CREATE SEQUENCE visual_presentation_epoch AS integer")
    op.execute(
        """
        SELECT setval(
            'visual_presentation_epoch',
            GREATEST(
                COALESCE((SELECT max(presentation_epoch) FROM visual_presentations), 0),
                COALESCE((SELECT max(presentation_epoch) FROM visual_playbacks), 0)
            ) + 1,
            false
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP SEQUENCE visual_presentation_epoch")
    op.drop_column("visual_presentations", "local_snapshot_json")
    op.execute("DROP TRIGGER visual_stage_preparation_inputs_changed ON ws_events")
    op.execute("DROP FUNCTION revise_visual_stage_preparation_inputs()")
    op.drop_table("visual_stage_preparation_inputs")
    op.drop_table("visual_stage_preparations")
