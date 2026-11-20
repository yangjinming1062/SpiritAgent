"""伙伴的分享来源、回复有效期和可恢复的资讯采集。"""

import json
import os
import tomllib
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from dotenv import dotenv_values
from pydantic import TypeAdapter
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 早期部署也标记为 baseline，但其基线含旧配额列，或尚无媒体选版证据。
    for table in ("life_action_admissions", "life_steps"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS budget_date, DROP COLUMN IF EXISTS is_creation")
    op.execute("ALTER TABLE post_publications DROP COLUMN IF EXISTS reserved_at")
    op.execute("ALTER TABLE video_gen_jobs ADD COLUMN IF NOT EXISTS selection_json JSONB")

    op.add_column("entry_comments", sa.Column("reply_expires_at", sa.DateTime(timezone=True)))
    op.execute("""
        UPDATE entry_comments SET reply_expires_at = created_at + interval '24 hours'
        WHERE reply_followup IS NOT NULL
    """)
    op.create_table(
        "companion_topic_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "batch_id", sa.BigInteger(), sa.ForeignKey("companion_topic_batches.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("candidate_key", sa.String(128), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("detailed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("selection", JSONB()),
        sa.UniqueConstraint("batch_id", "candidate_key", name="uq_topic_candidate_source"),
    )
    op.create_index(
        "ix_topic_candidates_pending",
        "companion_topic_candidates",
        ["batch_id", "id"],
        postgresql_where=sa.text("NOT reviewed"),
    )
    op.create_index(
        "ix_topic_batches_collecting",
        "companion_topic_batches",
        ["user_id", "activity_date"],
        postgresql_where=sa.text("status = 'collecting'"),
    )
    # 图片续行随消息选版登记。旧任务未绑定的槽位之后如获选，会在绑定时创建等待记录。
    op.execute("""
        DELETE FROM media_continuations w USING image_gen_jobs j
        WHERE w.owner_kind='chat.image' AND w.state='waiting' AND w.owner_job_id=j.id AND w.user_id=j.owner_id
          AND NOT (j.bindings_json ? (j.params_json->'media_ids'->>((w.context_json->>'slot')::int)))
    """)
    _move_topic_checkpoints()
    _upgrade_publications()
    _freeze_nightly_outreach()
    _migrate_collection_mode()
    op.execute("""
        DELETE FROM system_settings WHERE setting_key IN (
            'companion_max_pending_intents','cron_max_active_per_user','topic_search_requests_per_night',
            'topic_detail_requests_per_night','topic_candidate_limit'
        )
    """)


def _freeze_nightly_outreach() -> None:
    """旧待办在执行时按当前账户时区解释；已受理的生活步骤有准确的历史时刻。"""
    op.execute("""
        UPDATE nightly_activity_actions a SET arguments=jsonb_set(a.arguments,'{values}',
            ((a.arguments->'values') - 'local_time') || jsonb_build_object(
                'scheduled_at',s.action_json->'scheduled_at','expires_at',s.action_json->'expires_at'))
        FROM life_steps s
        WHERE a.capability='outreach.schedule' AND a.arguments->'values' ? 'local_time'
          AND s.id=a.life_step_id AND s.user_id=a.user_id
          AND s.action_json->>'scheduled_at' IS NOT NULL AND s.action_json->>'expires_at' IS NOT NULL
    """)
    # 旧 ZoneInfo 选择 fold=0。重复时刻选先发生的一次，跳时时刻按跳时前的偏移解释。
    op.execute("""
        CREATE FUNCTION pg_temp.first_wall_time(wall timestamp, zone text)
        RETURNS timestamptz LANGUAGE sql STABLE STRICT AS $tz$
            SELECT CASE WHEN previous AT TIME ZONE zone = wall THEN least(previous, ordinary) ELSE ordinary END
            FROM (SELECT wall AT TIME ZONE zone AS ordinary,
                         ((wall - interval '48 hours') AT TIME ZONE zone) + interval '48 hours' AS previous) t
        $tz$
    """)
    op.execute("""
        WITH pending AS (
            SELECT a.id,a.target_date,a.arguments #>> '{values,local_time}' AS wall_time,z.name AS zone
            FROM nightly_activity_actions a
            LEFT JOIN user_settings u ON u.user_id=a.user_id AND u.setting_key='timezone'
            LEFT JOIN pg_timezone_names z ON z.name=btrim(u.setting_value #>> '{}')
            WHERE a.capability='outreach.schedule' AND a.arguments->'values' ? 'local_time'
              AND a.life_step_id IS NULL AND a.status='pending'
        ), windows AS (
            SELECT id, CASE WHEN zone IS NOT NULL AND wall_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
                AND target_date BETWEEN date '0001-01-01' AND date '9999-12-29'
                THEN jsonb_build_object(
                    'scheduled_at',pg_temp.first_wall_time(target_date + 1 + wall_time::time, zone),
                    'expires_at',pg_temp.first_wall_time((target_date + 2)::timestamp, zone))
                END AS times FROM pending
        )
        UPDATE nightly_activity_actions a SET
            arguments=CASE WHEN w.times IS NULL THEN a.arguments ELSE
                jsonb_set(a.arguments,'{values}',((a.arguments->'values') - 'local_time') || w.times) END,
            status=CASE WHEN w.times IS NULL THEN 'blocked' ELSE a.status END,
            reason=CASE WHEN w.times IS NULL THEN '旧联系安排缺少可确定的本地时间或时区' ELSE a.reason END
        FROM windows w WHERE w.id=a.id
    """)
    op.execute("DROP FUNCTION pg_temp.first_wall_time(timestamp,text)")


def _migrate_collection_mode() -> None:
    # 按旧启动配置的优先级解析；单项数据库覆盖的优先级最高。
    directory = Path(__file__).resolve().parents[2]
    config_path = directory / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    values = {}
    for name, section in config.items():
        if isinstance(section, dict):
            values.update({key.upper(): value for key, value in section.items()})
        else:
            values[name.upper()] = section
    values.update({k.upper(): v for k, v in dotenv_values(directory / ".env").items()})
    values.update({k.upper(): v for k, v in os.environ.items()})
    if values.get("TOPIC_DISCOVERY_MODE"):
        return
    limits = {
        "topic_candidate_limit": 300,
        "topic_search_requests_per_night": 60,
        "topic_detail_requests_per_night": 60,
    }
    # 只将这三项部署值的整数传入 SQL。无效值仅在目标数据库没有更高优先级的覆盖时阻止升级。
    defaults = {}
    integer = TypeAdapter(int)
    for key, maximum in limits.items():
        if key.upper() in values:
            try:
                number = integer.validate_python(values[key.upper()])
                defaults[key] = number if 0 <= number <= maximum else -1
            except ValueError:
                defaults[key] = -1
    op.execute("""
        CREATE FUNCTION pg_temp.migrate_collection_mode(defaults jsonb)
        RETURNS void LANGUAGE plpgsql AS $mode$
        DECLARE
            cfg record;
            doc jsonb;
            literal text;
            n numeric;
            mode text := 'full';
        BEGIN
            IF EXISTS (SELECT FROM system_settings WHERE setting_key='topic_discovery_mode') THEN RETURN; END IF;
            FOR cfg IN
                SELECT k, ceiling, coalesce(s.setting_value, defaults->>k) AS raw FROM (VALUES
                    ('topic_candidate_limit',300), ('topic_search_requests_per_night',60),
                    ('topic_detail_requests_per_night',60)) v(k,ceiling)
                LEFT JOIN system_settings s ON s.setting_key=k
            LOOP
                IF cfg.raw IS NULL THEN CONTINUE; END IF;
                BEGIN
                    doc := cfg.raw::jsonb;
                    literal := regexp_replace(doc #>> '{}','^[[:space:]]+|[[:space:]]+$','','g');
                    n := CASE
                        WHEN jsonb_typeof(doc)='boolean' THEN (doc::boolean)::int
                        WHEN jsonb_typeof(doc)='number' THEN doc::numeric
                        WHEN jsonb_typeof(doc)='string' AND literal ~ '^[+-]?[0-9](_?[0-9])*([.]0(_?0)*)?$'
                            THEN replace(literal,'_','')::numeric END;
                EXCEPTION WHEN data_exception THEN n := NULL;
                END;
                IF n IS NULL OR n <> trunc(n) OR n NOT BETWEEN 0 AND cfg.ceiling THEN
                    RAISE EXCEPTION USING MESSAGE='旧采集设置 ' || cfg.k || ' 无法转换，请核对后重新升级';
                END IF;
                IF n=0 AND cfg.k <> 'topic_detail_requests_per_night' THEN mode := 'off';
                ELSIF n=0 AND mode='full' THEN mode := 'search_only'; END IF;
            END LOOP;
            IF mode <> 'full' THEN
                INSERT INTO system_settings(setting_key,setting_value) VALUES ('topic_discovery_mode',to_jsonb(mode)::text);
            END IF;
        END
        $mode$
    """)
    op.execute(sa.text("SELECT pg_temp.migrate_collection_mode(CAST(:v AS jsonb))").bindparams(v=json.dumps(defaults)))
    op.execute("DROP FUNCTION pg_temp.migrate_collection_mode(jsonb)")


def _move_topic_checkpoints() -> None:
    op.execute("""
        UPDATE companion_topic_batches SET status='failed', error='interrupted_before_snapshot'
        WHERE status='collecting' AND jsonb_typeof(checkpoint->'context') IS DISTINCT FROM 'object'
    """)
    move = sa.text("""
        WITH candidates AS (
            SELECT b.id, b.checkpoint, c.doc, c.n, (doc->>'platform') || ':' || (doc->>'source_id') AS key,
                nullif(doc->>'published_at','')::timestamptz >= b.window_start AND
                nullif(doc->>'published_at','')::timestamptz < b.window_end AND
                btrim(coalesce(doc->>'body','') || coalesce(doc->>'subtitle',''), :whitespace) <> '' AS eligible
            FROM companion_topic_batches b
            CROSS JOIN LATERAL jsonb_array_elements(b.checkpoint->'candidates') WITH ORDINALITY c(doc,n)
            WHERE b.status = 'collecting'
        ), ranked AS (
            SELECT *, count(*) FILTER (WHERE eligible) OVER (PARTITION BY id ORDER BY n) AS eligible_position
            FROM candidates
        )
        INSERT INTO companion_topic_candidates(batch_id,candidate_key,content,detailed,reviewed,selection)
        SELECT id,key,doc, coalesce((checkpoint->>'details_done')::boolean,false) OR EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(checkpoint->'details') WITH ORDINALITY d(k, pos)
            WHERE d.k = key AND d.pos <= (checkpoint->>'detail_index')::int
        ), coalesce(eligible AND eligible_position <= (checkpoint->>'selection_index')::int,false),
            (SELECT choice FROM jsonb_array_elements(nullif(checkpoint->'selections','null'::jsonb)) choice
             WHERE choice->>'candidate_id' = key LIMIT 1)
        FROM ranked ORDER BY id,n
    """)
    whitespace = "".join(chr(cp) for cp in range(0x110000) if chr(cp).isspace())
    op.execute(move.bindparams(whitespace=whitespace))
    op.execute("""
        UPDATE companion_topic_batches b SET checkpoint=CASE WHEN status='collecting' THEN
            (checkpoint - ARRAY['candidates','details','detail_index','selections','selection_index',
                                'search_done','details_done']::text[])
            || jsonb_build_object(
                'search_index', CASE WHEN (checkpoint->>'search_done')::boolean
                    THEN coalesce(jsonb_array_length(checkpoint->'queries'), 0) ELSE coalesce((checkpoint->>'search_index')::int, 0) END,
                'candidate_count', (SELECT count(*) FROM companion_topic_candidates WHERE batch_id=b.id),
                'eligible_count', (SELECT count(*) FROM companion_topic_candidates WHERE batch_id=b.id AND reviewed)
            )
            ELSE '{}'::jsonb END WHERE checkpoint <> '{}'::jsonb
    """)


def _upgrade_publications() -> None:
    # 送进构思的片段并不能证明已发表；旧计划的实际采用情况只能记为未知。
    op.execute("""UPDATE post_publications SET plan_json=plan_json || '{"excerpt_uses": null}'::jsonb
        WHERE jsonb_typeof(plan_json)='object'""")
    op.execute("""UPDATE companion_entries SET context_json=context_json || '{"used_excerpt_ids": null}'::jsonb
        WHERE kind='post'""")
    op.execute("""UPDATE media_continuations SET context_json=jsonb_set(context_json,'{plan,excerpt_uses}','null')
        WHERE owner_kind='post.publish' AND jsonb_typeof(context_json->'plan')='object'""")
    op.execute("""
        UPDATE post_publications p SET request_json = request_json || jsonb_build_object(
            'previous_post_choices','[]'::jsonb,'post_history_excerpted',false,
            'video_durations', CASE WHEN request_json->'available_types' ? 'video'
                THEN '{"plain":[6,10],"self":[6,10]}'::jsonb ELSE '{"plain":[],"self":[]}'::jsonb END,
            'selected_life_excerpts', coalesce((
                SELECT jsonb_agg(x.doc || jsonb_build_object('id',e.id::text,'source_id',e.fact_json->>'event_id') ORDER BY x.n)
                FROM (
                    SELECT doc, n, row_number() OVER (PARTITION BY doc ORDER BY n) AS occurrence
                    FROM jsonb_array_elements(request_json->'selected_life_excerpts') WITH ORDINALITY selected(doc,n)
                ) x
                JOIN LATERAL (
                    SELECT ev.id, ev.fact_json, count(*) OVER () AS matches,
                        row_number() OVER (ORDER BY ev.created_at DESC, ev.id DESC) AS occurrence
                    FROM life_events ev
                    WHERE ev.user_id=p.owner_id AND ev.kind='life.share.prepared' AND ev.fact_json->>'channel'='post'
                      AND (NOT coalesce(j.checkpoint ? 'excerpt_ids',false)
                           OR ev.id IN (SELECT v::bigint FROM jsonb_array_elements_text(j.checkpoint->'excerpt_ids') ids(v)))
                      AND ev.created_at >= (x.doc->>'selected_at')::timestamptz
                      AND ev.created_at < (x.doc->>'selected_at')::timestamptz + interval '1 second'
                      AND ev.narrative IS NOT DISTINCT FROM x.doc->>'narrative'
                      AND ev.fact_json->>'title' IS NOT DISTINCT FROM x.doc->>'title'
                      AND (NOT x.doc ? 'fact_type' OR ev.fact_type IS NOT DISTINCT FROM x.doc->>'fact_type')
                      AND (NOT x.doc ? 'occurred_at' OR
                           date_trunc('second',ev.occurred_at) IS NOT DISTINCT FROM (x.doc->>'occurred_at')::timestamptz)
                ) e ON e.occurrence=x.occurrence AND (e.matches=1 OR j.checkpoint ? 'excerpt_ids')
            ), '[]'::jsonb)
        ) FROM jobs j WHERE j.id=p.id AND request_json <> '{}'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        DO $restore$
        BEGIN
            IF EXISTS(SELECT FROM entry_comments WHERE reply_expires_at IS NOT NULL)
                OR EXISTS(SELECT FROM nightly_activity_actions WHERE capability='outreach.schedule')
                OR EXISTS(SELECT FROM companion_topic_batches)
                OR EXISTS(SELECT FROM post_publications)
                OR EXISTS(SELECT FROM companion_entries WHERE kind='post')
                OR EXISTS(SELECT FROM system_settings WHERE setting_key='topic_discovery_mode') THEN
                RAISE EXCEPTION '已转换的发布、跟进与采集数据须从升级前备份恢复';
            END IF;
        END
        $restore$
    """)
    op.drop_index("ix_topic_batches_collecting", table_name="companion_topic_batches")
    op.drop_table("companion_topic_candidates")
    op.drop_column("entry_comments", "reply_expires_at")
