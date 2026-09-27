"""保存动作的探身定位与内容轮廓。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("companion_actions", sa.Column("peek_geometry_json", sa.Text(), nullable=True))
    op.add_column("companion_actions", sa.Column("content_rect_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("companion_actions", "content_rect_json")
    op.drop_column("companion_actions", "peek_geometry_json")
