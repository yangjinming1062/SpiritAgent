"""Persist independent image regeneration state for existing scenes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("companion_scenes", sa.Column("regeneration_status", sa.String(length=24), nullable=True))
    op.add_column("companion_scenes", sa.Column("regeneration_stage", sa.String(length=24), nullable=True))
    op.add_column("companion_scenes", sa.Column("regeneration_error", sa.Text(), nullable=True))
    op.add_column("companion_scenes", sa.Column("regeneration_task_id", sa.String(length=36), nullable=True))
    op.add_column("companion_scenes", sa.Column("regeneration_state_json", sa.Text(), nullable=True))
    op.create_index(
        op.f("ix_companion_scenes_regeneration_status"),
        "companion_scenes",
        ["regeneration_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_companion_scenes_regeneration_status"), table_name="companion_scenes")
    op.drop_column("companion_scenes", "regeneration_state_json")
    op.drop_column("companion_scenes", "regeneration_task_id")
    op.drop_column("companion_scenes", "regeneration_error")
    op.drop_column("companion_scenes", "regeneration_stage")
    op.drop_column("companion_scenes", "regeneration_status")
