"""动作包外观激活代次。"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companion_action_packs",
        sa.Column("appearance_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    # 当前激活包记为第 1 代，其余包待下次激活再取用户最大代次 + 1；
    # 账本中已有意图的代次均为 0，不再匹配任何激活包，不会被补播。
    op.execute(
        sa.text("UPDATE companion_action_packs SET appearance_epoch = 1 WHERE active AND appearance_epoch = 0"),
    )


def downgrade() -> None:
    op.drop_column("companion_action_packs", "appearance_epoch")
