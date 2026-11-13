"""保存账户级默认桌面壁纸成品引用。"""

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("visual_presentations", sa.Column("default_visual_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_visual_presentations_default_visual_id",
        "visual_presentations",
        ["default_visual_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_presentation_default_visual_owner",
        "visual_presentations",
        "visual_items",
        ["user_id", "default_visual_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint("fk_presentation_default_visual_owner", "visual_presentations", type_="foreignkey")
    op.drop_index("ix_visual_presentations_default_visual_id", table_name="visual_presentations")
    op.drop_column("visual_presentations", "default_visual_id")
