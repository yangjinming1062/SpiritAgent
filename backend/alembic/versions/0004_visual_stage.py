"""结算缺少呈现宿主的待播指令，索引舞台素材。"""

import sqlalchemy as sa
from alembic import op

# Alembic 会按前缀解析修订，修订 ID 须与已发布的 0004 区分。
revision: str = "visual_stage_playback_0004"
down_revision: str = "0003"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        WITH unbound AS (
            SELECT user_id, pending_command_json ->> 'play_id' AS play_id
            FROM visual_presentations
            WHERE pending_command_json IS NOT NULL
              AND pending_command_json ->> 'target_surface' IS NULL
        ), expired AS (
            UPDATE visual_playbacks AS playback
            SET status = 'expired', error = '播放指令已失效，请重新播放', updated_at = now()
            FROM unbound
            WHERE playback.user_id = unbound.user_id
              AND playback.play_id = unbound.play_id
              AND playback.status IN ('queued', 'claimed')
        )
        UPDATE visual_presentations AS presentation
        SET pending_command_json = NULL
        FROM unbound
        WHERE presentation.user_id = unbound.user_id
        """
    )
    op.create_index(
        "ix_visual_versions_stage_character",
        "visual_versions",
        [
            "user_id",
            sa.text("(snapshot_json #>> '{source,identity_hash}')"),
            sa.text("(snapshot_json #>> '{source,outfit,content_hash}')"),
            "id",
        ],
        postgresql_where=sa.text(
            "(snapshot_json #>> '{source,identity_hash}') IS NOT NULL"
            " AND (snapshot_json #>> '{source,outfit,content_hash}') IS NOT NULL"
        ),
    )


def downgrade() -> None:
    # 已结算的播放状态在 0003 也有效。
    op.drop_index("ix_visual_versions_stage_character", table_name="visual_versions")
