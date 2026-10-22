"""衣柜使用静态造型，退役窗口动作及其独占资产。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 待回收引用与数据退役同事务；文件由启动后的账户引用核验负责删除。
    op.execute(
        sa.text(
            r"""
        CREATE TEMP TABLE wardrobe_retired_assets (user_id INTEGER, value TEXT) ON COMMIT DROP;
        INSERT INTO wardrobe_retired_assets SELECT user_id, to_jsonb(p)::text FROM companion_action_packs p;
        INSERT INTO wardrobe_retired_assets SELECT user_id, to_jsonb(a)::text FROM companion_actions a;
        INSERT INTO wardrobe_retired_assets SELECT user_id, path FROM action_asset_retirements;
        INSERT INTO wardrobe_retired_assets
            SELECT user_id, to_jsonb(q)::text FROM video_queue_requests q WHERE kind = 'action';
        INSERT INTO wardrobe_retired_assets
            SELECT user_id, payload FROM ws_events
            WHERE event_type LIKE 'companion.video.%' OR event_type LIKE 'companion.action.%';

        CREATE TEMP TABLE wardrobe_desktop_definitions ON COMMIT DROP AS
        WITH RECURSIVE required(definition_id, set_id) AS (
            SELECT d.id, s.id FROM action_definitions d
                JOIN desktop_video_sets s ON s.id = d.origin_target_id AND s.user_id = d.user_id
            UNION
            SELECT t.definition_id, s.id FROM action_sync_targets t
                JOIN desktop_video_sets s ON s.id = t.target_id AND s.user_id = t.user_id
            UNION
            SELECT a.definition_id, a.set_id FROM desktop_video_actions a WHERE a.definition_id IS NOT NULL
            UNION
            SELECT d.id, r.set_id FROM required r
                JOIN action_definitions d ON d.reuse_definition_id = r.definition_id
        ) SELECT definition_id AS id, min(set_id) AS set_id FROM required GROUP BY definition_id;

        INSERT INTO wardrobe_retired_assets
            SELECT user_id, review_source_json FROM action_definitions
            WHERE id NOT IN (SELECT id FROM wardrobe_desktop_definitions);
        DELETE FROM action_sync_targets WHERE target_kind = 'window';
        UPDATE action_definitions d SET
            origin_target_id = k.set_id,
            review_source_json = jsonb_set(d.review_source_json::jsonb - 'target_kind',
                '{target_id}', to_jsonb(k.set_id))::text
        FROM wardrobe_desktop_definitions k WHERE d.id = k.id
            AND NOT EXISTS (
                SELECT 1 FROM desktop_video_sets s WHERE s.id = d.origin_target_id AND s.user_id = d.user_id);
        UPDATE action_definitions SET review_source_json = (review_source_json::jsonb - 'target_kind')::text
        WHERE review_source_json::jsonb ? 'target_kind';
        DELETE FROM action_definitions WHERE id NOT IN (SELECT id FROM wardrobe_desktop_definitions);

        DELETE FROM ws_events WHERE event_type LIKE 'companion.video.%' OR event_type LIKE 'companion.action.%';
        UPDATE video_queue_requests SET cancel_requested = TRUE, entity_id = NULL,
            request_json = NULL, result_json = NULL, revision = revision + 1, updated_at = now(),
            state = CASE WHEN state IN ('queued', 'rate_limited', 'paused', 'claimed')
                THEN 'cancelled' ELSE state END,
            frozen_json = jsonb_set(frozen_json::jsonb, '{task}', '"desktop_life"'::jsonb)::text
        WHERE kind = 'action';
        DELETE FROM video_queue_requests WHERE kind = 'action'
            AND state IN ('succeeded', 'failed', 'cancelled', 'stopped');

        INSERT INTO asset_cleanup_pending (user_id, path)
        SELECT DISTINCT r.user_id, 'companion-assets/' || r.user_id || '/' || split_part(m[3], '?', 1)
        FROM wardrobe_retired_assets r,
            LATERAL regexp_matches(r.value,
                $rx$(companion-assets/|/api/companion/asset/)([0-9]+)/([^[:space:]"'<>\\,;{}()\[\]]+)$rx$, 'g') m
        WHERE m[2] = r.user_id::text
            AND split_part(m[3], '?', 1) !~ '(^|/)([.]|[.][.])(/|$)'
        ON CONFLICT (user_id, path) DO UPDATE SET not_before = LEAST(asset_cleanup_pending.not_before, now());
            """,
        ),
    )
    op.drop_table("action_playbacks")
    op.drop_table("action_asset_retirements")
    op.drop_table("companion_actions")
    op.drop_table("companion_action_packs")
    op.drop_column("companion_outfits", "initial_video_started")
    op.drop_column("companion_outfits", "initial_video_error")
    # 目标类别随窗口动作退役，只剩桌面套装一种归属。
    op.drop_column("action_definitions", "origin_kind")
    op.drop_constraint("ck_action_sync_target_kind", "action_sync_targets", type_="check")
    op.drop_constraint("uq_action_sync_target", "action_sync_targets", type_="unique")
    op.drop_column("action_sync_targets", "target_kind")
    op.create_unique_constraint("uq_action_sync_target", "action_sync_targets", ["definition_id", "target_id"])
    op.execute(
        sa.text(
            r"""
        UPDATE user_model_configs SET ai_config = (ai_config::jsonb #- '{tasks,window_action}')::json
        WHERE ai_config::jsonb #> '{tasks,window_action}' IS NOT NULL;
        UPDATE system_settings SET setting_value = (setting_value::jsonb #- '{tasks,window_action}')::text
        WHERE setting_key = 'ai_config' AND setting_value::jsonb #> '{tasks,window_action}' IS NOT NULL;
            """,
        ),
    )


def downgrade() -> None:
    raise RuntimeError("窗口动作数据及无引用媒体已退役；恢复旧版本须使用升级前的完整备份")
