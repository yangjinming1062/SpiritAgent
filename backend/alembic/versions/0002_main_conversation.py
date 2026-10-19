"""仅陪伴主会话归属系统对话。"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE conversations SET kind = 'standard', is_deletable = TRUE, is_renamable = TRUE "
        "WHERE kind = 'special' AND system_preset_id != 'companion'",
    )
    op.create_check_constraint(
        "ck_conversations_main_preset",
        "conversations",
        "kind != 'special' OR system_preset_id = 'companion'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_conversations_main_preset", "conversations", type_="check")
