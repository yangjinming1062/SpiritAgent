"""记录回答与图片实际使用的模型，支持按用户意图选择后续模型。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("messages", "generation_origin_json"),
    ("avatar_assets", "generation_origin_json"),
    ("avatar_assets", "fullbody_generation_origin_json"),
    ("companion_fullbody_candidates", "generation_origin_json"),
    ("companion_scenes", "generation_origin_json"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.add_column(table, sa.Column(column, sa.Text(), nullable=True))
    op.add_column("prompt_submissions", sa.Column("replace_reply_message_id", sa.Integer(), nullable=True))
    op.add_column("prompt_submissions", sa.Column("result_message_id", sa.Integer(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("history_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("conversations", "history_revision")
    op.drop_column("prompt_submissions", "result_message_id")
    op.drop_column("prompt_submissions", "replace_reply_message_id")
    for table, column in reversed(_COLUMNS):
        op.drop_column(table, column)
