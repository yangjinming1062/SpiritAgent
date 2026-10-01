"""action_playbacks.pack_id 索引；删除只写不读的列并清洗状态 JSON 中的 result_file_id 键"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(op.f("ix_action_playbacks_pack_id"), "action_playbacks", ["pack_id"], unique=False)
    op.drop_index(op.f("ix_messages_summary_date"), table_name="messages")
    op.drop_column("messages", "summary_date")
    op.drop_column("avatar_assets", "style")
    op.drop_column("avatar_assets", "seed")
    op.drop_column("companion_fullbody_candidates", "body_source_hash")
    op.drop_column("companion_character_cards", "portrait_source_hash")
    op.drop_column("companion_character_cards", "body_source_hash")
    op.drop_column("companion_character_cards", "portrait_pending_hash")
    op.drop_column("companion_character_cards", "body_pending_hash")
    op.drop_column("conversations", "cwd")
    op.drop_column("login_records", "last_seen_at")
    op.drop_column("nightly_activity_actions", "started_at")
    op.drop_column("nightly_activity_actions", "finished_at")
    op.drop_column("companion_diary_entries", "memory_ids")
    # MediaChainState 删除 result_file_id 且禁止多余键；存量状态 JSON 需去掉该键后才能按新模型解析。
    for table in ("video_gen_jobs", "companion_actions"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET generation_state_json = (generation_state_json::jsonb - 'result_file_id')::text "
                "WHERE generation_state_json IS NOT NULL AND generation_state_json LIKE '%result_file_id%'",
            ),
        )


def downgrade() -> None:
    op.add_column(
        "companion_diary_entries",
        sa.Column("memory_ids", sa.ARRAY(sa.String()), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column(
        "nightly_activity_actions",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "nightly_activity_actions",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("login_records", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("cwd", sa.String(length=1024), nullable=True))
    op.add_column(
        "companion_character_cards",
        sa.Column("body_pending_hash", sa.String(64), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "companion_character_cards",
        sa.Column("portrait_pending_hash", sa.String(64), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "companion_character_cards",
        sa.Column("body_source_hash", sa.String(64), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "companion_character_cards",
        sa.Column("portrait_source_hash", sa.String(64), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "companion_fullbody_candidates",
        sa.Column("body_source_hash", sa.String(length=64), server_default=sa.text("''"), nullable=False),
    )
    op.add_column("avatar_assets", sa.Column("seed", sa.Integer(), nullable=True))
    op.add_column(
        "avatar_assets",
        sa.Column("style", sa.String(length=64), nullable=False, server_default=sa.text("''")),
    )
    op.add_column("messages", sa.Column("summary_date", sa.String(length=10), nullable=True))
    op.create_index(op.f("ix_messages_summary_date"), "messages", ["summary_date"], unique=False)
    op.drop_index(op.f("ix_action_playbacks_pack_id"), table_name="action_playbacks")
