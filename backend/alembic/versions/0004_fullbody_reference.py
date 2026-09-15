"""为角色增加独立于建模种子的全身参考图。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "avatar_assets",
        sa.Column("seed_fullbody_url", sa.String(length=2048), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_column("avatar_assets", "seed_fullbody_url")
