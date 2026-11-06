"""动作登记的幂等键。

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("action_definitions", sa.Column("request_key", sa.String(length=128), nullable=True))
    op.create_unique_constraint("uq_action_definitions_request", "action_definitions", ["user_id", "request_key"])


def downgrade() -> None:
    op.drop_constraint("uq_action_definitions_request", "action_definitions", type_="unique")
    op.drop_column("action_definitions", "request_key")
