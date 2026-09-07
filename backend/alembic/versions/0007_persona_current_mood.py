"""add current_mood to personas

持久化伙伴当前由 LLM 推理得到的心境与心理活动说明，打通情境推理端到端呈现。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("personas", sa.Column("current_mood", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("personas", "current_mood")
