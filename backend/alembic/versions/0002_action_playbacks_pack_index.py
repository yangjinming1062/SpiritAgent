"""action_playbacks.pack_id 索引：删除动作包时按外键级联清理播放账本"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(op.f("ix_action_playbacks_pack_id"), "action_playbacks", ["pack_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_action_playbacks_pack_id"), table_name="action_playbacks")
