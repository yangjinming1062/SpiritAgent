"""房间图行新增 source 列：区分 AI 生成与用户自备图（等待上传的 pending 行不参与生成恢复）。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companion_room_backdrops",
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'generated'")),
    )


def downgrade() -> None:
    op.drop_column("companion_room_backdrops", "source")
