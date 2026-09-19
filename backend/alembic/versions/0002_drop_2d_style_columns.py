"""删除 companion_outfits / companion_2d_models 的 style 列。

两表只服务 2D 链，画风由服务端固定为 `anime_2d_illustration`（`MESH2D_STYLE`，
see-through 动漫域约束见 docs/PIPELINE.md §6.1），列值恒为常量、无快照意义。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic 用的版本标识符。
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("companion_outfits", "style")
    op.drop_column("companion_2d_models", "style")


def downgrade() -> None:
    op.add_column(
        "companion_2d_models",
        sa.Column("style", sa.String(length=32), server_default=sa.text("'refined_anime_cg'"), nullable=False),
    )
    op.add_column(
        "companion_outfits",
        sa.Column("style", sa.String(length=32), server_default=sa.text("'refined_anime_cg'"), nullable=False),
    )
