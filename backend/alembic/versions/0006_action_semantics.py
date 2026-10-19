"""共享定义仅保存动作语义，触发策略由具体画面版本拥有。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE desktop_video_actions SET allowed_triggers_json = :triggers "
            "WHERE definition_id IS NOT NULL AND preset = FALSE",
        ).bindparams(triggers='["periodic", "user_return", "chat", "manual"]'),
    )
    op.drop_column("action_definitions", "desktop_continuity")
    op.drop_column("action_definitions", "desktop_allowed_triggers_json")


def downgrade() -> None:
    raise NotImplementedError("动作语义调整不可逆，请恢复升级前的数据库备份")
