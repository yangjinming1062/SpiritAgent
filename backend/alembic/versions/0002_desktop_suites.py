"""独立桌景套装与图片确认；旧桌景定向重置，来源库保留。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _reset_desktop() -> None:
    # 只登记桌景专属目录下的引用。回收器仍会核对全部业务活引用和写入状态。
    op.execute(
        sa.text("""
        WITH progress_documents AS (
            SELECT user_id, generation_state_json::jsonb AS value
            FROM desktop_video_actions WHERE generation_state_json IS NOT NULL
        ), chain_documents AS (
            SELECT user_id, (value->>'image_chain_json')::jsonb AS value FROM progress_documents
            WHERE NULLIF(value->>'image_chain_json', '') IS NOT NULL
            UNION ALL
            SELECT user_id, (value->>'video_chain_json')::jsonb FROM progress_documents
            WHERE NULLIF(value->>'video_chain_json', '') IS NOT NULL
        ), documents AS (
            SELECT user_id, context_json::jsonb AS value FROM desktop_video_sets
            UNION ALL SELECT user_id, to_jsonb(base_image_path) FROM desktop_video_sets WHERE base_image_path IS NOT NULL
            UNION ALL SELECT user_id, value FROM progress_documents
            UNION ALL SELECT user_id, value FROM chain_documents
            UNION ALL
            SELECT user_id, (candidate->>'result_json')::jsonb FROM chain_documents
            CROSS JOIN LATERAL jsonb_array_elements(COALESCE(value->'candidates', '[]'::jsonb)) AS candidate
            WHERE NULLIF(candidate->>'result_json', '') IS NOT NULL
            UNION ALL SELECT user_id, accepted_asset_json::jsonb FROM desktop_video_actions WHERE accepted_asset_json IS NOT NULL
            UNION ALL SELECT user_id, design_json::jsonb FROM desktop_video_proposals
        ), owned_references AS (
            SELECT user_id, trim(both '"' from item::text) AS path
            FROM documents CROSS JOIN LATERAL jsonb_path_query(value, '$.** ? (@.type() == "string")') AS item
        )
        INSERT INTO asset_cleanup_pending (user_id, path, not_before)
        SELECT DISTINCT user_id, path, now() + interval '24 hours' FROM owned_references
        WHERE path LIKE 'companion-assets/' || user_id::text || '/desktop/%'
          AND path !~ '(^|/)\\.\\.?(/|$)'
        ON CONFLICT (user_id, path) DO UPDATE
        SET not_before = GREATEST(asset_cleanup_pending.not_before, EXCLUDED.not_before)
    """),
    )
    for table in (
        "desktop_video_playbacks",
        "desktop_video_proposals",
        "desktop_video_states",
        "desktop_video_actions",
        "desktop_video_sets",
    ):
        op.execute(sa.text(f"DELETE FROM {table}"))
    op.execute(sa.text("DELETE FROM ws_events WHERE starts_with(event_type, 'companion.desktop_video.')"))


def upgrade() -> None:
    _reset_desktop()
    op.drop_constraint("uq_desktop_video_sets_context", "desktop_video_sets", type_="unique")
    op.add_column("desktop_video_sets", sa.Column("request_id", sa.String(80), nullable=True))
    op.add_column(
        "desktop_video_sets",
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
    )
    op.add_column(
        "desktop_video_sets",
        sa.Column("source", sa.String(24), server_default=sa.text("'ai'"), nullable=False),
    )
    op.create_unique_constraint("uq_desktop_video_sets_request", "desktop_video_sets", ["user_id", "request_id"])
    for field in ("needs_update", "update_pending"):
        op.add_column(
            "desktop_video_actions",
            sa.Column(field, sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        )
    op.add_column(
        "desktop_video_states",
        sa.Column("default_created", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
    )


def downgrade() -> None:
    _reset_desktop()
    op.drop_column("desktop_video_states", "default_created")
    for field in ("update_pending", "needs_update"):
        op.drop_column("desktop_video_actions", field)
    op.drop_constraint("uq_desktop_video_sets_request", "desktop_video_sets", type_="unique")
    op.create_unique_constraint("uq_desktop_video_sets_context", "desktop_video_sets", ["user_id", "context_hash"])
    for field in ("source", "is_default", "request_id"):
        op.drop_column("desktop_video_sets", field)
