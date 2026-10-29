"""创建完整空库结构、派生统计及事务通知。"""

from collections.abc import Sequence

import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "action_definitions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("motion_description", sa.Text(), nullable=False),
        sa.Column(
            "use_when_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column(
            "avoid_when_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column(
            "allowed_triggers_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text('\'["chat", "manual"]\''),
            nullable=False,
        ),
        sa.Column("clip_kind", sa.String(length=8), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "semantic_tags_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("performance_role", sa.String(length=12), server_default=sa.text("'reaction'"), nullable=False),
        sa.Column("motion_intensity", sa.String(length=12), server_default=sa.text("'normal'"), nullable=False),
        sa.Column("interruptible", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("preset_key", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("review_reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("review_source_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("action_definitions_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_action_definitions_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_action_definitions_source"),
        sa.UniqueConstraint("user_id", "preset_key", name="uq_action_definitions_preset"),
        sa.CheckConstraint(
            "performance_role IN ('idle', 'sustained', 'reaction')",
            name=op.f("ck_action_definitions_action_performance_role"),
        ),
        sa.CheckConstraint(
            "motion_intensity IN ('subtle', 'normal', 'expressive')",
            name=op.f("ck_action_definitions_action_motion_intensity"),
        ),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("token_jti", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("admin_sessions_pkey")),
        sa.UniqueConstraint("token_jti", name="uq_admin_sessions_token_jti"),
    )
    op.create_table(
        "appearance_assets",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("avatar_id", sa.BigInteger(), nullable=True),
        sa.Column("outfit_id", sa.BigInteger(), nullable=True),
        sa.Column("candidate_id", sa.BigInteger(), nullable=True),
        sa.Column("analysis_job_id", sa.BigInteger(), nullable=True),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.CheckConstraint(
            "num_nonnulls(avatar_id, outfit_id, candidate_id, analysis_job_id) = 1",
            name=op.f("ck_appearance_assets_appearance_asset_owner"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("appearance_assets_pkey")),
        sa.UniqueConstraint("avatar_id", "asset_id", name="uq_avatar_asset_binding"),
        sa.UniqueConstraint("candidate_id", "asset_id", name="uq_candidate_asset_binding"),
        sa.UniqueConstraint("outfit_id", "asset_id", name="uq_outfit_asset_binding"),
        sa.UniqueConstraint("user_id", "id", name="uq_appearance_assets_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_appearance_assets_source"),
    )
    op.create_table(
        "assets",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("media_type", sa.String(length=128), server_default="application/octet-stream", nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=16), server_default="ready", nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("state IN ('ready', 'cleanup', 'deleted')", name=op.f("ck_assets_asset_state")),
        sa.CheckConstraint("size_bytes >= 0 AND attempts >= 0", name=op.f("ck_assets_asset_numbers")),
        sa.PrimaryKeyConstraint("id", name=op.f("assets_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_assets_user_id"),
        sa.UniqueConstraint("user_id", "path", name="uq_assets_user_path"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_assets_source"),
    )
    op.create_table(
        "avatar_assets",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("asset_url", sa.String(length=2048), nullable=False),
        sa.Column("portrait_asset_id", sa.BigInteger(), nullable=True),
        sa.Column("fullbody_asset_id", sa.BigInteger(), nullable=True),
        sa.Column("generation_origin_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column(
            "fullbody_generation_origin_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("seed_fullbody_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("is_fullbody_confirmed", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "automatic_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column(
            "overrides_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("analysis_job_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("avatar_assets_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_avatar_assets_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_avatar_assets_source"),
    )
    op.create_table(
        "character_analysis_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("avatar_id", sa.BigInteger(), nullable=True),
        sa.Column("extraction_id", sa.String(length=36), nullable=False),
        sa.Column("portrait_status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("body_status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "portrait_result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "body_result_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("portrait_source_path", sa.String(length=2048), nullable=False),
        sa.Column("body_source_path", sa.String(length=2048), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("character_analysis_jobs_pkey")),
        sa.UniqueConstraint("owner_id", "id", name="uq_character_analysis_jobs_owner_id"),
    )
    op.create_table(
        "companion_entries",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("content_type", sa.String(length=16), server_default=sa.text("'text'"), nullable=False),
        sa.Column("title", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "content_type IN ('text', 'image', 'video', 'audio')", name=op.f("ck_companion_entries_entry_content_type")
        ),
        sa.CheckConstraint("kind IN ('post', 'diary')", name=op.f("ck_companion_entries_entry_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_entries_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_entries_user_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_entries_source"),
    )
    op.create_table(
        "companion_fullbody_candidates",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("avatar_id", sa.BigInteger(), nullable=False),
        sa.Column("base_fullbody_url", sa.String(length=2048), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.String(length=2048), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=True),
        sa.Column("base_asset_id", sa.BigInteger(), nullable=True),
        sa.Column("generation_origin_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column(
            "body_features_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_fullbody_candidates_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_companion_fullbody_candidates_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_companion_fullbody_candidates_source"),
    )
    op.create_table(
        "companion_intents",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=True),
        sa.Column("cron_job_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'waiting'"), nullable=False),
        sa.Column("not_before_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wake_event", sa.String(length=32), nullable=True),
        sa.Column("event_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "cron_job_id IS NULL OR source_kind IS NOT DISTINCT FROM 'cron'",
            name=op.f("ck_companion_intents_intent_cron_source"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_intents_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_companion_intents_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_companion_intents_source"),
    )
    op.create_table(
        "companion_outfits",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("description_status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("description_error", sa.Text(), nullable=True),
        sa.Column("fullbody_url", sa.String(length=2048), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
        sa.Column(
            "source_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("is_initial", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_outfits_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_companion_outfits_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_companion_outfits_source"),
    )
    op.create_table(
        "companion_scenes",
        sa.Column("source_key", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("origin", sa.String(length=16), server_default=sa.text("'user_request'"), nullable=False),
        sa.Column("source", sa.String(length=16), server_default=sa.text("'generated'"), nullable=False),
        sa.Column("target_size_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("source_size_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("image_size_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("title", sa.String(length=80), server_default=sa.text("''"), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("requirements", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("prompt", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("media_asset_id", sa.BigInteger(), nullable=True),
        sa.Column("upload_asset_id", sa.BigInteger(), nullable=True),
        sa.Column("media_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("generation_origin_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("upload_source_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("reference_image", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initial_job_id", sa.BigInteger(), nullable=True),
        sa.Column("replacement_job_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_scenes_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_companion_scenes_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_companion_scenes_user_source"),
    )
    op.create_table(
        "companion_topic_batches",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'collecting'"), nullable=False),
        sa.Column("search_requests", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("detail_requests", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.String(length=160), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_topic_batches_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_companion_topic_batches_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_companion_topic_batches_source"),
        sa.UniqueConstraint("user_id", "system_preset_id", "activity_date", name="uq_topic_batch_scope_date"),
    )
    op.create_table(
        "companion_topic_feedback",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.String(length=8), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_topic_feedback_pkey")),
        sa.UniqueConstraint("topic_id", name="uq_topic_feedback_topic"),
        sa.UniqueConstraint("user_id", "id", name="uq_companion_topic_feedback_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_companion_topic_feedback_source"),
    )
    op.create_table(
        "companion_topic_usages",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_topic_usages_pkey")),
        sa.UniqueConstraint("topic_id", "message_id", name="uq_topic_usage_message"),
        sa.UniqueConstraint("user_id", "id", name="uq_companion_topic_usages_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_companion_topic_usages_source"),
    )
    op.create_table(
        "companion_topics",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=96), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("author", sa.String(length=160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_type", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("user_relevance", sa.Text(), nullable=False),
        sa.Column("companion_interest", sa.Text(), nullable=False),
        sa.Column("conversation_angle", sa.Text(), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_topics_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_companion_topics_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_companion_topics_source"),
        sa.UniqueConstraint("user_id", "system_preset_id", "platform", "source_id", name="uq_topic_scope_source"),
    )
    op.create_table(
        "conversations",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("forked_from_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(length=32), server_default=sa.text("'standard'"), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("memory_reviewed_message_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("context_after_message_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("history_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("visible_message_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("input_token_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_token_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("tool_call_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settings_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("is_automation", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("is_deletable", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("is_renamable", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "is_automation = (system_preset_id = 'automation')",
            name=op.f("ck_conversations_ck_conversations_automation_preset"),
        ),
        sa.CheckConstraint(
            "kind != 'special' OR system_preset_id = 'companion'",
            name=op.f("ck_conversations_ck_conversations_main_preset"),
        ),
        sa.CheckConstraint(
            "context_after_message_id >= 0", name=op.f("ck_conversations_ck_conversations_context_watermark")
        ),
        sa.CheckConstraint(
            "visible_message_count >= 0 AND input_token_count >= 0 AND output_token_count >= 0 AND tool_call_count >= 0",
            name=op.f("ck_conversations_ck_conversations_statistics_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("conversations_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_conversations_user_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_conversations_source"),
    )
    op.create_table(
        "cron_jobs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("schedule", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default=sa.text("'standard'"), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=True),
        sa.Column("is_paused", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("one_shot", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("cron_jobs_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_cron_jobs_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_cron_jobs_source"),
    )
    op.create_table(
        "custom_voices",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("model_card_id", sa.String(length=36), nullable=False),
        sa.Column("provider_voice_id", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("reference_path", sa.Text(), nullable=True),
        sa.Column("preview_path", sa.Text(), nullable=False),
        sa.Column("reference_asset_id", sa.BigInteger(), nullable=True),
        sa.Column("preview_asset_id", sa.BigInteger(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expected_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expected_voice_id", sa.Text(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('sample', 'design', 'clone')", name=op.f("ck_custom_voices_voice_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("custom_voices_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_custom_voices_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_custom_voices_source"),
    )
    op.create_table(
        "entry_assets",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_entry_assets_entry_asset_position")),
        sa.PrimaryKeyConstraint("id", name=op.f("entry_assets_pkey")),
        sa.UniqueConstraint("entry_id", "role", "position", name="uq_entry_asset_role"),
        sa.UniqueConstraint("user_id", "id", name="uq_entry_assets_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_entry_assets_source"),
    )
    op.create_table(
        "entry_comments",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("post_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reply_to_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("reply_status", sa.String(length=16), server_default=sa.text("'none'"), nullable=False),
        sa.Column("reply_error", sa.String(length=160), nullable=True),
        sa.Column("reply_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(role = 'companion') = (reply_status = 'none')", name=op.f("ck_entry_comments_entry_comment_delivery")
        ),
        sa.CheckConstraint(
            "reply_status IN ('none', 'pending', 'running', 'completed', 'failed')",
            name=op.f("ck_entry_comments_entry_comment_state"),
        ),
        sa.CheckConstraint("role IN ('user', 'companion')", name=op.f("ck_entry_comments_entry_comment_role")),
        sa.CheckConstraint("reply_attempts >= 0", name=op.f("ck_entry_comments_entry_comment_attempts")),
        sa.PrimaryKeyConstraint("id", name=op.f("entry_comments_pkey")),
        sa.UniqueConstraint("reply_to_comment_id", name="uq_entry_comment_reply"),
        sa.UniqueConstraint("user_id", "id", name="uq_entry_comments_owner_id"),
        sa.UniqueConstraint("user_id", "post_id", "id", name="uq_entry_comment_scope"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_entry_comment_source"),
    )
    op.create_table(
        "entry_links",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), server_default=sa.text("'diary_source'"), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.CheckConstraint("position >= 0 AND source_id != target_id", name=op.f("ck_entry_links_entry_link_position")),
        sa.PrimaryKeyConstraint("id", name=op.f("entry_links_pkey")),
        sa.UniqueConstraint("source_id", "target_id", "kind", name="uq_entry_link"),
        sa.UniqueConstraint("user_id", "id", name="uq_entry_links_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_entry_links_source"),
    )
    op.create_table(
        "job_assets",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("job_assets_pkey")),
        sa.UniqueConstraint("job_id", "asset_id", "role", name="uq_job_assets_role"),
        sa.UniqueConstraint("user_id", "id", name="uq_job_assets_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_job_assets_source"),
    )
    op.create_table(
        "job_requests",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(length=256), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("job_requests_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_job_requests_owner_id"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_job_requests_user_request"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_job_requests_source"),
    )
    op.create_table(
        "jobs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(length=48), server_default="prepare", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("cleanup_pending", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("checkpoint", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued','running','paused','unknown','succeeded','failed','cancelled')",
            name=op.f("ck_jobs_job_state"),
        ),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_jobs_job_revision")),
        sa.PrimaryKeyConstraint("id", name=op.f("jobs_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_jobs_user_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_jobs_user_source"),
    )
    op.create_table(
        "life_events",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_key", sa.String(length=180), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("source", sa.String(length=48), nullable=False),
        sa.Column("subject", sa.String(length=24), server_default=sa.text("'companion'"), nullable=False),
        sa.Column("fact_type", sa.String(length=24), server_default="execution", nullable=False),
        sa.Column("fact_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("pursuit_id", sa.BigInteger(), nullable=True),
        sa.Column("step_id", sa.BigInteger(), nullable=True),
        sa.Column("result_job_id", sa.BigInteger(), nullable=True),
        sa.Column("result_visual_id", sa.BigInteger(), nullable=True),
        sa.Column("result_visual_version_id", sa.BigInteger(), nullable=True),
        sa.Column("result_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("result_outfit_id", sa.BigInteger(), nullable=True),
        sa.Column("result_scene_id", sa.BigInteger(), nullable=True),
        sa.Column("result_action_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "result_ref_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("is_meaningful", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "fact_type IN ('intent', 'virtual_activity', 'execution', 'result')", name=op.f("ck_life_events_fact_type")
        ),
        sa.CheckConstraint("subject IN ('companion', 'user', 'system')", name=op.f("ck_life_events_event_subject")),
        sa.PrimaryKeyConstraint("id", name=op.f("life_events_pkey")),
        sa.UniqueConstraint("user_id", "event_key", name="uq_life_events_user_key"),
        sa.UniqueConstraint("user_id", "id", name="uq_life_events_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_life_events_source"),
    )
    op.create_table(
        "life_pursuits",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
        sa.Column("title", sa.String(length=160), server_default=sa.text("''"), nullable=False),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("desired_result", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("progress", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("next_step", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("status_source", sa.String(length=16), server_default="companion", nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resume_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previous_pursuit_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "result_ref_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'abandoned')", name=op.f("ck_life_pursuits_pursuit_status")
        ),
        sa.CheckConstraint("status_source IN ('user', 'companion')", name=op.f("ck_life_pursuits_status_source")),
        sa.PrimaryKeyConstraint("id", name=op.f("life_pursuits_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_life_pursuits_owner_id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_life_pursuits_user_idempotency"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_life_pursuits_source"),
    )
    op.create_table(
        "life_states",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("current_activity", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("activity_kind", sa.String(length=24), server_default="virtual_activity", nullable=False),
        sa.Column("activity_type", sa.String(length=16), server_default="idle", nullable=False),
        sa.Column("scene_tags_json", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("activity_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activity_step_id", sa.BigInteger(), nullable=True),
        sa.Column("presentation_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("presentation_evaluated_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "current_scene_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("mood", sa.Text(), nullable=True),
        sa.Column("active_pursuit_id", sa.BigInteger(), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("scene_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "activity_kind IN ('intent', 'virtual_activity', 'execution', 'result')",
            name=op.f("ck_life_states_activity_kind"),
        ),
        sa.CheckConstraint(
            "activity_type IN ('idle','reading','working','creating','thinking','resting','organizing','socializing')",
            name=op.f("ck_life_states_activity_type"),
        ),
        sa.CheckConstraint(
            "revision >= 0 AND scene_revision >= 0 AND presentation_revision >= 0 AND presentation_evaluated_revision >= 0",
            name=op.f("ck_life_states_revisions_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("life_states_pkey")),
        sa.UniqueConstraint("user_id", "source_key", name="uq_life_states_source"),
    )
    op.create_table(
        "life_steps",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("pursuit_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("state", sa.String(length=16), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("action_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("reading_result_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("disposition", sa.String(length=24), server_default="ready", nullable=False),
        sa.Column("wait_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("budget_date", sa.Date(), nullable=True),
        sa.Column("is_creation", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("source", sa.String(length=24), server_default="life", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("business_action_id", sa.BigInteger(), nullable=True),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("generation_id", sa.String(length=64), nullable=True),
        sa.Column("result_job_id", sa.BigInteger(), nullable=True),
        sa.Column("result_visual_id", sa.BigInteger(), nullable=True),
        sa.Column("result_visual_version_id", sa.BigInteger(), nullable=True),
        sa.Column("result_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("result_outfit_id", sa.BigInteger(), nullable=True),
        sa.Column("result_scene_id", sa.BigInteger(), nullable=True),
        sa.Column("result_action_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "result_ref_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('ready','waiting','permission_required','unavailable','executing','done','failed','cancelled','unknown')",
            name=op.f("ck_life_steps_step_disposition"),
        ),
        sa.CheckConstraint(
            "reading_result_json IS NULL OR kind = 'content.read'", name=op.f("ck_life_steps_reading_result_kind")
        ),
        sa.CheckConstraint(
            "state IN ('planned', 'running', 'completed', 'failed', 'cancelled', 'unknown')",
            name=op.f("ck_life_steps_step_state"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_life_steps_step_attempts")),
        sa.PrimaryKeyConstraint("id", name=op.f("life_steps_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_life_steps_owner_id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_life_steps_user_idempotency"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_life_steps_source"),
    )
    op.create_table(
        "login_records",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_jti", sa.String(length=64), nullable=False),
        sa.Column("client_version", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("login_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("logout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("login_records_pkey")),
        sa.UniqueConstraint("token_jti", name="uq_login_records_token_jti"),
        sa.UniqueConstraint("user_id", "id", name="uq_login_records_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_login_records_source"),
    )
    op.create_table(
        "memories",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.String(length=36), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), server_default="recall", nullable=False),
        sa.Column("slot_key", sa.String(length=256), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_conversation_id", sa.BigInteger(), nullable=True),
        sa.Column("source_event_id", sa.BigInteger(), nullable=True),
        sa.Column("source_batch_id", sa.String(length=128), nullable=True),
        sa.Column("local_date", sa.Date(), nullable=True),
        sa.Column("content_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("basis", sa.String(length=16), server_default="system", nullable=False),
        sa.Column("usage", sa.String(length=16), server_default="contextual", nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), server_default="", nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("importance", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("basis IN ('explicit', 'inferred', 'observed', 'system')", name=op.f("ck_memories_basis")),
        sa.CheckConstraint("jsonb_typeof(tags) = 'array'", name=op.f("ck_memories_tags_array")),
        sa.CheckConstraint(
            "source_event_id IS NULL OR system_preset_id = 'companion'", name=op.f("ck_memories_life_source_scope")
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'active', 'invalidated', 'forgotten')", name=op.f("ck_memories_status")
        ),
        sa.CheckConstraint("usage IN ('contextual', 'background')", name=op.f("ck_memories_usage")),
        sa.CheckConstraint("content_version > 0", name=op.f("ck_memories_content_version")),
        sa.PrimaryKeyConstraint("id", name=op.f("memories_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_memories_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_memories_source_key"),
        sa.UniqueConstraint("user_id", "system_preset_id", "kind", "slot_key", name="uq_memories_scope_slot"),
        sa.UniqueConstraint("user_id", "system_preset_id", "source_event_id", name="uq_memories_source_event"),
    )
    op.create_table(
        "memory_embeddings",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("memory_id", sa.BigInteger(), nullable=False),
        sa.Column("model_key", sa.String(length=64), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.CheckConstraint("content_version > 0", name=op.f("ck_memory_embeddings_content_version")),
        sa.CheckConstraint(
            "dimensions > 0 AND vector_dims(embedding) = dimensions", name=op.f("ck_memory_embeddings_dimensions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("memory_embeddings_pkey")),
        sa.UniqueConstraint("memory_id", "model_key", name="uq_memory_embedding_model"),
        sa.UniqueConstraint("user_id", "id", name="uq_memory_embeddings_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_memory_embeddings_source"),
    )
    op.create_table(
        "memory_evidence",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("memory_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("stance", sa.String(length=16), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.CheckConstraint("stance IN ('supports', 'opposes')", name=op.f("ck_memory_evidence_stance")),
        sa.PrimaryKeyConstraint("id", name=op.f("memory_evidence_pkey")),
        sa.UniqueConstraint("memory_id", "fingerprint", "quote", "stance", name="uq_memory_evidence_quote"),
        sa.UniqueConstraint("user_id", "id", name="uq_memory_evidence_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_memory_evidence_source"),
    )
    op.create_table(
        "memory_fingerprints",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("memory_id", sa.BigInteger(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("suppressed", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("memory_fingerprints_pkey")),
        sa.UniqueConstraint("memory_id", "fingerprint", name="uq_memory_fingerprint_source"),
        sa.UniqueConstraint("user_id", "id", name="uq_memory_fingerprints_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_memory_fingerprints_source"),
    )
    op.create_table(
        "memory_revisions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("memory_id", sa.BigInteger(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.CheckConstraint("content_version > 0", name=op.f("ck_memory_revisions_revision_content_version")),
        sa.PrimaryKeyConstraint("id", name=op.f("memory_revisions_pkey")),
        sa.UniqueConstraint("memory_id", "content_version", name="uq_memory_revision_version"),
        sa.UniqueConstraint("user_id", "id", name="uq_memory_revisions_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_memory_revisions_source"),
    )
    op.create_table(
        "message_assets",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("message_assets_pkey")),
        sa.UniqueConstraint("message_id", "asset_id", name="uq_message_asset"),
        sa.UniqueConstraint("user_id", "id", name="uq_message_assets_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_message_assets_source"),
    )
    op.create_table(
        "message_voice_refs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("voice_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("message_voice_refs_pkey")),
        sa.UniqueConstraint("message_id", "voice_id", name="uq_message_voice_ref"),
        sa.UniqueConstraint("user_id", "id", name="uq_message_voice_refs_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_message_voice_refs_source"),
    )
    op.create_table(
        "messages",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("subtype", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("tool_call_id", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("turn_duration_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("content_type", sa.String(length=32), server_default=sa.text("'text'"), nullable=False),
        sa.Column("media_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("reasoning_content", sa.Text(), nullable=True),
        sa.Column("generation_origin_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("reply_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("summary_through_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.CheckConstraint(
            "(content_type = 'companion_reply') = (reply_json IS NOT NULL)",
            name=op.f("ck_messages_ck_messages_reply_delivery"),
        ),
        sa.CheckConstraint(
            "content_type != 'companion_reply' OR (role = 'assistant' AND content IS NOT NULL AND tool_calls IS NULL)",
            name=op.f("ck_messages_ck_messages_reply_content"),
        ),
        sa.CheckConstraint(
            "content_type IN ('text', 'multimodal_v1', 'companion_reply')",
            name=op.f("ck_messages_ck_messages_content_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("messages_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_messages_user_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_messages_source"),
    )
    op.create_table(
        "nightly_activity_actions",
        sa.Column("log_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("action_key", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "arguments", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("result", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("nightly_activity_actions_pkey")),
        sa.UniqueConstraint("log_id", "action_key", name="uq_nightly_activity_actions_log_key"),
        sa.UniqueConstraint("user_id", "id", name="uq_nightly_activity_actions_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_nightly_activity_actions_source"),
    )
    op.create_table(
        "nightly_activity_logs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'running'"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("nightly_activity_logs_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_nightly_activity_logs_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_nightly_activity_logs_source"),
        sa.UniqueConstraint("user_id", "target_date", name="uq_nightly_activity_logs_user_date"),
    )
    op.create_table(
        "personas",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("definition_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "personality_tags_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("is_complete", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("is_portrait_confirmed", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("portrait_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scene_policy", sa.String(length=16), server_default=sa.text("'llm_may_replace'"), nullable=False),
        sa.Column("outfit_policy", sa.String(length=16), server_default=sa.text("'llm_may_replace'"), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("personas_pkey")),
        sa.UniqueConstraint("user_id", "source_key", name="uq_personas_source"),
    )
    op.create_table(
        "post_publications",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("video_job_id", sa.BigInteger(), nullable=True),
        sa.Column("post_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("post_publications_pkey")),
        sa.UniqueConstraint("owner_id", "id", name="uq_post_publications_owner_id"),
    )
    op.create_table(
        "prompt_submissions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'accepted'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("origin_kind", sa.String(length=16), nullable=False),
        sa.Column("origin_id", sa.String(length=64), nullable=True),
        sa.Column("replace_reply_message_id", sa.BigInteger(), nullable=True),
        sa.Column("result_message_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("prompt_submissions_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_prompt_submissions_owner_id"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_prompt_submissions_user_request"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_prompt_submissions_source"),
    )
    op.create_table(
        "provider_attempts",
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("lane_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("owner_kind", sa.String(length=32), nullable=False),
        sa.Column("capability", sa.String(length=16), server_default="video", nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("frozen_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("result_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("error_status_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("rate_limit_count", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("provider_attempts_pkey")),
        sa.UniqueConstraint("generation_id", "attempt_index", name="uq_video_queue_attempt"),
        sa.UniqueConstraint("token", name=op.f("uq_provider_attempts_token")),
        sa.UniqueConstraint("user_id", "id", name="uq_provider_attempts_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_provider_attempts_source"),
    )
    op.create_table(
        "provider_lanes",
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("provider_lanes_pkey")),
        sa.UniqueConstraint("provider", "credential_fingerprint", name="uq_video_queue_credential"),
        sa.UniqueConstraint("token", name=op.f("uq_provider_lanes_token")),
    )
    op.create_table(
        "pursuit_evidence",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("pursuit_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pursuit_evidence_pkey")),
        sa.UniqueConstraint("pursuit_id", "event_id", name="uq_pursuit_evidence_event"),
        sa.UniqueConstraint("user_id", "id", name="uq_pursuit_evidence_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_pursuit_evidence_source"),
    )
    op.create_table(
        "remote_pairings",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("remote_pairings_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_remote_pairings_token_hash")),
        sa.UniqueConstraint("user_id", "id", name="uq_remote_pairings_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_remote_pairings_source"),
    )
    op.create_table(
        "remote_sessions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("remote_sessions_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_remote_sessions_token_hash")),
        sa.UniqueConstraint("user_id", "id", name="uq_remote_sessions_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_remote_sessions_source"),
    )
    op.create_table(
        "scene_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("scene_id", sa.BigInteger(), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("token", sa.String(length=36), nullable=False),
        sa.Column("availability", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("scene_jobs_pkey")),
        sa.UniqueConstraint("owner_id", "id", name="uq_scene_jobs_owner_id"),
        sa.UniqueConstraint("owner_id", "token", name="uq_scene_jobs_owner_token"),
    )
    op.create_table(
        "submission_messages",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("submission_messages_pkey")),
        sa.UniqueConstraint("submission_id", "message_id", name="uq_submission_message"),
        sa.UniqueConstraint("submission_id", "position", name="uq_submission_message_position"),
        sa.UniqueConstraint("user_id", "id", name="uq_submission_messages_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_submission_messages_source"),
    )
    op.create_table(
        "system_settings",
        sa.Column("setting_key", sa.String(length=128), nullable=False),
        sa.Column("setting_value", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("system_settings_pkey")),
    )
    op.create_table(
        "update_versions",
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("release_notes", sa.Text(), nullable=False),
        sa.Column("exe_filename", sa.String(length=256), nullable=False),
        sa.Column("exe_sha512", sa.String(length=128), nullable=False),
        sa.Column("exe_size", sa.Integer(), nullable=False),
        sa.Column("mac_filename", sa.String(length=256), nullable=True),
        sa.Column("mac_sha512", sa.String(length=128), nullable=True),
        sa.Column("mac_size", sa.Integer(), nullable=True),
        sa.Column("runner_filename", sa.String(length=256), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("update_versions_pkey")),
    )
    op.create_table(
        "user_model_configs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("ai_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("user_model_configs_pkey")),
        sa.UniqueConstraint("user_id", "source_key", name="uq_user_model_configs_source"),
    )
    op.create_table(
        "user_social_accounts",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("credentials", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("revision", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("login_revision", sa.String(length=36), nullable=True),
        sa.Column("login_session_id", sa.String(length=96), nullable=True),
        sa.Column("login_state", sa.String(length=32), nullable=True),
        sa.Column("login_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("login_verification_error", sa.String(length=24), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "state IN ('unconfigured', 'configured', 'login_required')",
            name=op.f("ck_user_social_accounts_social_account_state"),
        ),
        sa.PrimaryKeyConstraint("id", name="user_social_accounts_pkey"),
        sa.UniqueConstraint("user_id", "platform", name="uq_user_social_accounts_platform"),
        sa.UniqueConstraint("user_id", "id", name="uq_user_social_accounts_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_user_social_accounts_source"),
    )
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("setting_key", sa.String(length=128), nullable=False),
        sa.Column("setting_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("user_settings_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_user_settings_owner_id"),
        sa.UniqueConstraint("user_id", "setting_key", name="uq_user_settings_user_key"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_user_settings_source"),
    )
    op.create_table(
        "users",
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("activation_code", sa.Text(), nullable=False),
        sa.Column("activation_token_hash", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("nightly_activity_enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("users_pkey")),
    )
    op.create_table(
        "video_gen_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=True),
        sa.Column("structured_reply", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("media_id", sa.String(length=128), nullable=True),
        sa.Column("reply_message_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("cleanup_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("params_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("candidate_video_url", sa.Text(), nullable=True),
        sa.Column("generation_attempt_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("video_gen_jobs_pkey")),
        sa.UniqueConstraint("owner_id", "id", name="uq_video_gen_jobs_owner_id"),
        sa.UniqueConstraint("owner_id", "media_id", name="uq_video_jobs_owner_media"),
    )
    op.create_table(
        "visual_items",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.String(length=36), nullable=False),
        sa.Column("media_kind", sa.String(length=8), nullable=False),
        sa.Column("asset_kind", sa.String(length=16), server_default="'cinematic'", nullable=False),
        sa.Column("title", sa.String(length=160), server_default="", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="ready", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("clip_kind", sa.String(length=8), server_default="loop", nullable=True),
        sa.Column("allowed_triggers", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("outfit_id", sa.BigInteger(), nullable=True),
        sa.Column("scene_id", sa.BigInteger(), nullable=True),
        sa.Column("action_id", sa.BigInteger(), nullable=True),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("visual_items_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_visual_items_user_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_visual_items_user_source"),
        sa.CheckConstraint("asset_kind <> 'actor' OR media_kind = 'video'", name="ck_visual_items_actor_video"),
    )
    op.create_table(
        "visual_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_kind", sa.String(length=16), server_default=sa.text("'cinematic'"), nullable=False),
        sa.Column("outfit_id", sa.BigInteger(), nullable=True),
        sa.Column("scene_id", sa.BigInteger(), nullable=True),
        sa.Column("action_id", sa.BigInteger(), nullable=True),
        sa.Column("target_visual_id", sa.BigInteger(), nullable=True),
        sa.Column("purpose", sa.String(length=16), server_default=sa.text("'create'"), nullable=False),
        sa.Column("target_kind", sa.String(length=8), server_default=sa.text("'video'"), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.Column("snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feedback", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("video_feedback", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("candidates_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("repair_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("repair_limit", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("result_visual_id", sa.BigInteger(), nullable=True),
        sa.Column("result_image_id", sa.BigInteger(), nullable=True),
        sa.Column("result_version_id", sa.BigInteger(), nullable=True),
        sa.Column("result_image_version_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("visual_jobs_pkey")),
        sa.UniqueConstraint("owner_id", "id", name="uq_visual_jobs_owner_id"),
    )
    op.create_table(
        "visual_playbacks",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("visual_id", sa.BigInteger(), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("visual_version", sa.Integer(), nullable=False),
        sa.Column("play_id", sa.String(length=36), nullable=False),
        sa.Column("presentation_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("state_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("selection", sa.String(length=16), server_default=sa.text("'base'"), nullable=False),
        sa.Column("life_revision", sa.Integer(), nullable=True),
        sa.Column("life_activity_type", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_kind", sa.String(length=24), nullable=True),
        sa.Column("snapshot_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("visual_playbacks_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_visual_playbacks_owner_id"),
        sa.UniqueConstraint("user_id", "play_id", name="uq_visual_playbacks_user_play"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_visual_playbacks_source"),
    )
    op.create_table(
        "visual_presentations",
        sa.Column("pending_static_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("current_static_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("composition_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("intent_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column("loop_version_id", sa.BigInteger(), nullable=True),
        sa.Column("current_visual_id", sa.BigInteger(), nullable=True),
        sa.Column("loop_visual_id", sa.BigInteger(), nullable=True),
        sa.Column("autonomous_enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("target_width", sa.Integer(), nullable=True),
        sa.Column("target_height", sa.Integer(), nullable=True),
        sa.Column("presentation_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("state_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pending_command_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("current_snapshot_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("loop_snapshot_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("visual_presentations_pkey")),
        sa.UniqueConstraint("user_id", "source_key", name="uq_visual_presentations_source"),
    )
    op.create_table(
        "visual_version_assets",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("visual_version_assets_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_visual_version_assets_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_visual_version_assets_source"),
        sa.UniqueConstraint("version_id", "role", "asset_id", name="uq_visual_version_asset_role"),
    )
    op.create_table(
        "visual_versions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("visual_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), server_default="1", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("outfit_id", sa.BigInteger(), nullable=True),
        sa.Column("scene_id", sa.BigInteger(), nullable=True),
        sa.Column("action_id", sa.BigInteger(), nullable=True),
        sa.Column("media_path", sa.Text(), nullable=False),
        sa.Column("poster_path", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("outfit_description", sa.Text(), server_default="", nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("body_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("input_image_id", sa.BigInteger(), nullable=True),
        sa.Column("input_image_version_id", sa.BigInteger(), nullable=True),
        sa.Column("input_image_path", sa.Text(), nullable=True),
        sa.Column("input_image_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.CheckConstraint(
            "number > 0 AND width > 0 AND height > 0", name=op.f("ck_visual_versions_visual_version_dimensions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("visual_versions_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_visual_versions_user_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_visual_versions_source"),
        sa.UniqueConstraint("visual_id", "id", name="uq_visual_versions_item_id"),
        sa.UniqueConstraint("visual_id", "number", name="uq_visual_versions_number"),
    )
    op.create_table(
        "ws_events",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("ws_events_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_ws_events_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_ws_events_source"),
    )
    op.create_table(
        "outfit_creation_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("result_outfit_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("outfit_creation_jobs_pkey")),
        sa.UniqueConstraint("owner_id", "id", name="uq_outfit_creation_jobs_owner_id"),
    )
    op.create_table(
        "life_action_admissions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("budget_date", sa.Date(), nullable=False),
        sa.Column("is_creation", sa.Boolean(), nullable=False),
        sa.Column("submitted", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "source_key", sa.String(length=36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("life_action_admissions_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_life_action_admissions_owner_id"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_life_admission_request"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_life_action_admissions_source"),
    )
    op.create_foreign_key(
        op.f("life_action_admissions_user_id_fkey"),
        "life_action_admissions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_life_admission_budget", "life_action_admissions", ["user_id", "budget_date"], unique=False)
    op.create_foreign_key(
        "fk_life_event_result_version",
        "life_events",
        "visual_versions",
        ["result_visual_id", "result_visual_version_id"],
        ["visual_id", "id"],
        ondelete="SET NULL",
        initially="DEFERRED",
        deferrable=True,
        match="FULL",
    )
    op.create_foreign_key(
        "fk_life_events_pursuit_id_owner",
        "life_events",
        "life_pursuits",
        ["user_id", "pursuit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (pursuit_id)",
    )
    op.create_foreign_key(
        "fk_life_events_result_action_id_owner",
        "life_events",
        "action_definitions",
        ["user_id", "result_action_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_action_id)",
    )
    op.create_foreign_key(
        "fk_life_events_result_entry_id_owner",
        "life_events",
        "companion_entries",
        ["user_id", "result_entry_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_entry_id)",
    )
    op.create_foreign_key(
        "fk_life_events_result_job_id_owner",
        "life_events",
        "jobs",
        ["user_id", "result_job_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_job_id)",
    )
    op.create_foreign_key(
        "fk_life_events_result_outfit_id_owner",
        "life_events",
        "companion_outfits",
        ["user_id", "result_outfit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_outfit_id)",
    )
    op.create_foreign_key(
        "fk_life_events_result_scene_id_owner",
        "life_events",
        "companion_scenes",
        ["user_id", "result_scene_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_scene_id)",
    )
    op.create_foreign_key(
        "fk_life_events_result_visual_id_owner",
        "life_events",
        "visual_items",
        ["user_id", "result_visual_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_life_events_result_visual_version_id_owner",
        "life_events",
        "visual_versions",
        ["user_id", "result_visual_version_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_life_events_step_id_owner",
        "life_events",
        "life_steps",
        ["user_id", "step_id"],
        ["user_id", "id"],
        ondelete="SET NULL (step_id)",
    )
    op.create_foreign_key(
        op.f("life_events_user_id_fkey"), "life_events", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index(op.f("ix_life_events_occurred_at"), "life_events", ["occurred_at"], unique=False)
    op.create_index(op.f("ix_life_events_pursuit_id"), "life_events", ["pursuit_id"], unique=False)
    op.create_index(
        "ix_life_events_pursuit_occurred",
        "life_events",
        ["user_id", "pursuit_id", sa.literal_column("occurred_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(op.f("ix_life_events_step_id"), "life_events", ["step_id"], unique=False)
    op.create_index(op.f("ix_life_events_user_id"), "life_events", ["user_id"], unique=False)
    op.create_index("ix_life_events_user_kind", "life_events", ["user_id", "kind"], unique=False)
    op.create_index(
        "ix_life_events_user_occurred",
        "life_events",
        ["user_id", sa.literal_column("occurred_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_foreign_key(
        "fk_life_pursuits_previous_pursuit_id_owner",
        "life_pursuits",
        "life_pursuits",
        ["user_id", "previous_pursuit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (previous_pursuit_id)",
    )
    op.create_foreign_key(
        op.f("life_pursuits_user_id_fkey"), "life_pursuits", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index(op.f("ix_life_pursuits_status"), "life_pursuits", ["status"], unique=False)
    op.create_index(op.f("ix_life_pursuits_user_id"), "life_pursuits", ["user_id"], unique=False)
    op.create_index(
        "ix_life_pursuits_user_status",
        "life_pursuits",
        ["user_id", "status", sa.literal_column("updated_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_life_pursuits_user_updated",
        "life_pursuits",
        ["user_id", sa.literal_column("updated_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_foreign_key(
        "fk_life_states_active_pursuit_id_owner",
        "life_states",
        "life_pursuits",
        ["user_id", "active_pursuit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (active_pursuit_id)",
    )
    op.create_foreign_key(
        "fk_life_states_activity_step_id_owner",
        "life_states",
        "life_steps",
        ["user_id", "activity_step_id"],
        ["user_id", "id"],
        ondelete="SET NULL (activity_step_id)",
    )
    op.create_foreign_key(
        op.f("life_states_user_id_fkey"), "life_states", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index(op.f("ix_life_states_active_pursuit_id"), "life_states", ["active_pursuit_id"], unique=False)
    op.create_index(op.f("ix_life_states_next_review_at"), "life_states", ["next_review_at"], unique=False)
    op.create_foreign_key(
        "fk_life_step_result_version",
        "life_steps",
        "visual_versions",
        ["result_visual_id", "result_visual_version_id"],
        ["visual_id", "id"],
        ondelete="SET NULL",
        initially="DEFERRED",
        deferrable=True,
        match="FULL",
    )
    op.create_foreign_key(
        "fk_life_steps_business_action_id_owner",
        "life_steps",
        "action_definitions",
        ["user_id", "business_action_id"],
        ["user_id", "id"],
        ondelete="SET NULL (business_action_id)",
    )
    op.create_foreign_key(
        "fk_life_steps_job_id_owner",
        "life_steps",
        "jobs",
        ["user_id", "job_id"],
        ["user_id", "id"],
        ondelete="SET NULL (job_id)",
    )
    op.create_foreign_key(
        "fk_life_steps_pursuit_id_owner",
        "life_steps",
        "life_pursuits",
        ["user_id", "pursuit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (pursuit_id)",
    )
    op.create_foreign_key(
        "fk_life_steps_result_action_id_owner",
        "life_steps",
        "action_definitions",
        ["user_id", "result_action_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_action_id)",
    )
    op.create_foreign_key(
        "fk_life_steps_result_entry_id_owner",
        "life_steps",
        "companion_entries",
        ["user_id", "result_entry_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_entry_id)",
    )
    op.create_foreign_key(
        "fk_life_steps_result_job_id_owner",
        "life_steps",
        "jobs",
        ["user_id", "result_job_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_job_id)",
    )
    op.create_foreign_key(
        "fk_life_steps_result_outfit_id_owner",
        "life_steps",
        "companion_outfits",
        ["user_id", "result_outfit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_outfit_id)",
    )
    op.create_foreign_key(
        "fk_life_steps_result_scene_id_owner",
        "life_steps",
        "companion_scenes",
        ["user_id", "result_scene_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_scene_id)",
    )
    op.create_foreign_key(
        "fk_life_steps_result_visual_id_owner",
        "life_steps",
        "visual_items",
        ["user_id", "result_visual_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_life_steps_result_visual_version_id_owner",
        "life_steps",
        "visual_versions",
        ["user_id", "result_visual_version_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        op.f("life_steps_user_id_fkey"), "life_steps", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_life_steps_budget", "life_steps", ["user_id", "budget_date"], unique=False)
    op.create_index("ix_life_steps_due", "life_steps", ["state", "not_before", "user_id"], unique=False)
    op.create_index(op.f("ix_life_steps_job_id"), "life_steps", ["job_id"], unique=False)
    op.create_index(op.f("ix_life_steps_pursuit_id"), "life_steps", ["pursuit_id"], unique=False)
    op.create_index(op.f("ix_life_steps_state"), "life_steps", ["state"], unique=False)
    op.create_index(op.f("ix_life_steps_user_id"), "life_steps", ["user_id"], unique=False)
    op.create_index("ix_life_steps_user_state", "life_steps", ["user_id", "state"], unique=False)
    op.create_foreign_key(
        "fk_outfit_creation_jobs_id_owner",
        "outfit_creation_jobs",
        "jobs",
        ["owner_id", "id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_outfit_creation_jobs_result_outfit_id_owner",
        "outfit_creation_jobs",
        "companion_outfits",
        ["owner_id", "result_outfit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_outfit_id)",
    )
    op.create_foreign_key(
        "fk_presentation_current_pair",
        "visual_presentations",
        "visual_versions",
        ["current_visual_id", "current_version_id"],
        ["visual_id", "id"],
        ondelete="SET NULL",
        initially="DEFERRED",
        deferrable=True,
        match="FULL",
    )
    op.create_foreign_key(
        "fk_presentation_loop_pair",
        "visual_presentations",
        "visual_versions",
        ["loop_visual_id", "loop_version_id"],
        ["visual_id", "id"],
        ondelete="SET NULL",
        initially="DEFERRED",
        deferrable=True,
        match="FULL",
    )
    op.create_foreign_key(
        "fk_visual_presentations_current_visual_id_owner",
        "visual_presentations",
        "visual_items",
        ["user_id", "current_visual_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_visual_presentations_loop_visual_id_owner",
        "visual_presentations",
        "visual_items",
        ["user_id", "loop_visual_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        op.f("visual_presentations_user_id_fkey"),
        "visual_presentations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("action_definitions_user_id_fkey"), "action_definitions", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        op.f("appearance_assets_user_id_fkey"), "appearance_assets", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_appearance_assets_analysis_job_id_owner",
        "appearance_assets",
        "character_analysis_jobs",
        ["user_id", "analysis_job_id"],
        ["owner_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_appearance_assets_asset_id_owner",
        "appearance_assets",
        "assets",
        ["user_id", "asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_appearance_assets_avatar_id_owner",
        "appearance_assets",
        "avatar_assets",
        ["user_id", "avatar_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_appearance_assets_candidate_id_owner",
        "appearance_assets",
        "companion_fullbody_candidates",
        ["user_id", "candidate_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_appearance_assets_outfit_id_owner",
        "appearance_assets",
        "companion_outfits",
        ["user_id", "outfit_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(op.f("assets_user_id_fkey"), "assets", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        op.f("avatar_assets_user_id_fkey"), "avatar_assets", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_avatar_assets_analysis_job_id_owner",
        "avatar_assets",
        "character_analysis_jobs",
        ["user_id", "analysis_job_id"],
        ["owner_id", "id"],
        ondelete="SET NULL (analysis_job_id)",
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_avatar_assets_fullbody_asset_id_owner",
        "avatar_assets",
        "assets",
        ["user_id", "fullbody_asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_avatar_assets_portrait_asset_id_owner",
        "avatar_assets",
        "assets",
        ["user_id", "portrait_asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_character_analysis_jobs_avatar_id_owner",
        "character_analysis_jobs",
        "avatar_assets",
        ["owner_id", "avatar_id"],
        ["user_id", "id"],
        ondelete="SET NULL (avatar_id)",
    )
    op.create_foreign_key(
        "fk_character_analysis_jobs_id_owner",
        "character_analysis_jobs",
        "jobs",
        ["owner_id", "id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("companion_entries_user_id_fkey"), "companion_entries", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        op.f("companion_fullbody_candidates_user_id_fkey"),
        "companion_fullbody_candidates",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_companion_fullbody_candidates_asset_id_owner",
        "companion_fullbody_candidates",
        "assets",
        ["user_id", "asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_companion_fullbody_candidates_avatar_id_owner",
        "companion_fullbody_candidates",
        "avatar_assets",
        ["user_id", "avatar_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_companion_fullbody_candidates_base_asset_id_owner",
        "companion_fullbody_candidates",
        "assets",
        ["user_id", "base_asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("companion_intents_user_id_fkey"), "companion_intents", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_companion_intents_cron_job_id_owner",
        "companion_intents",
        "cron_jobs",
        ["user_id", "cron_job_id"],
        ["user_id", "id"],
        ondelete="SET NULL (cron_job_id)",
    )
    op.create_foreign_key(
        op.f("companion_outfits_user_id_fkey"), "companion_outfits", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_companion_outfits_asset_id_owner",
        "companion_outfits",
        "assets",
        ["user_id", "asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("companion_scenes_user_id_fkey"), "companion_scenes", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_companion_scenes_initial_job_id_owner",
        "companion_scenes",
        "scene_jobs",
        ["user_id", "initial_job_id"],
        ["owner_id", "id"],
        ondelete="SET NULL (initial_job_id)",
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_companion_scenes_media_asset_id_owner",
        "companion_scenes",
        "assets",
        ["user_id", "media_asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_companion_scenes_replacement_job_id_owner",
        "companion_scenes",
        "scene_jobs",
        ["user_id", "replacement_job_id"],
        ["owner_id", "id"],
        ondelete="SET NULL (replacement_job_id)",
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_companion_scenes_upload_asset_id_owner",
        "companion_scenes",
        "assets",
        ["user_id", "upload_asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("companion_topic_batches_user_id_fkey"),
        "companion_topic_batches",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("companion_topic_feedback_user_id_fkey"),
        "companion_topic_feedback",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_companion_topic_feedback_topic_id_owner",
        "companion_topic_feedback",
        "companion_topics",
        ["user_id", "topic_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("companion_topic_usages_user_id_fkey"),
        "companion_topic_usages",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_companion_topic_usages_message_id_owner",
        "companion_topic_usages",
        "messages",
        ["user_id", "message_id"],
        ["user_id", "id"],
        ondelete="SET NULL (message_id)",
    )
    op.create_foreign_key(
        "fk_companion_topic_usages_topic_id_owner",
        "companion_topic_usages",
        "companion_topics",
        ["user_id", "topic_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("companion_topics_user_id_fkey"), "companion_topics", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_companion_topics_batch_id_owner",
        "companion_topics",
        "companion_topic_batches",
        ["user_id", "batch_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("conversations_user_id_fkey"), "conversations", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_conversations_forked_from_id_owner",
        "conversations",
        "conversations",
        ["user_id", "forked_from_id"],
        ["user_id", "id"],
        ondelete="SET NULL (forked_from_id)",
    )
    op.create_foreign_key(
        "fk_conversations_parent_id_owner",
        "conversations",
        "conversations",
        ["user_id", "parent_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(op.f("cron_jobs_user_id_fkey"), "cron_jobs", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        "fk_cron_jobs_conversation_id_owner",
        "cron_jobs",
        "conversations",
        ["user_id", "conversation_id"],
        ["user_id", "id"],
        ondelete="SET NULL (conversation_id)",
    )
    op.create_foreign_key(
        op.f("custom_voices_user_id_fkey"), "custom_voices", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_custom_voices_preview_asset_id_owner",
        "custom_voices",
        "assets",
        ["user_id", "preview_asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_custom_voices_reference_asset_id_owner",
        "custom_voices",
        "assets",
        ["user_id", "reference_asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("entry_assets_user_id_fkey"), "entry_assets", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_entry_assets_asset",
        "entry_assets",
        "assets",
        ["user_id", "asset_id"],
        ["user_id", "id"],
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_entry_assets_entry",
        "entry_assets",
        "companion_entries",
        ["user_id", "entry_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("entry_comments_user_id_fkey"), "entry_comments", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_entry_comments_entry",
        "entry_comments",
        "companion_entries",
        ["user_id", "post_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_entry_comments_reply",
        "entry_comments",
        "entry_comments",
        ["user_id", "post_id", "reply_to_comment_id"],
        ["user_id", "post_id", "id"],
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        op.f("entry_links_user_id_fkey"), "entry_links", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_entry_links_source",
        "entry_links",
        "companion_entries",
        ["user_id", "source_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_entry_links_target",
        "entry_links",
        "companion_entries",
        ["user_id", "target_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_assets_owned_asset",
        "job_assets",
        "assets",
        ["user_id", "asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_job_assets_owned_job", "job_assets", "jobs", ["user_id", "job_id"], ["user_id", "id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        op.f("job_assets_user_id_fkey"), "job_assets", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_job_requests_owned_job",
        "job_requests",
        "jobs",
        ["user_id", "job_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("job_requests_user_id_fkey"), "job_requests", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(op.f("jobs_user_id_fkey"), "jobs", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        op.f("login_records_user_id_fkey"), "login_records", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_memories_source_conversation",
        "memories",
        "conversations",
        ["user_id", "source_conversation_id"],
        ["user_id", "id"],
        ondelete="SET NULL (source_conversation_id)",
    )
    op.create_foreign_key(
        "fk_memories_source_event",
        "memories",
        "life_events",
        ["user_id", "source_event_id"],
        ["user_id", "id"],
        ondelete="SET NULL (source_event_id)",
    )
    op.create_foreign_key(op.f("memories_user_id_fkey"), "memories", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        "fk_memory_embedding_owner",
        "memory_embeddings",
        "memories",
        ["user_id", "memory_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("memory_embeddings_user_id_fkey"), "memory_embeddings", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_memory_evidence_message_owner",
        "memory_evidence",
        "messages",
        ["user_id", "message_id"],
        ["user_id", "id"],
        ondelete="SET NULL (message_id)",
    )
    op.create_foreign_key(
        "fk_memory_evidence_owner",
        "memory_evidence",
        "memories",
        ["user_id", "memory_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("memory_evidence_user_id_fkey"), "memory_evidence", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_memory_fingerprint_owner",
        "memory_fingerprints",
        "memories",
        ["user_id", "memory_id"],
        ["user_id", "id"],
        ondelete="SET NULL (memory_id)",
    )
    op.create_foreign_key(
        op.f("memory_fingerprints_user_id_fkey"),
        "memory_fingerprints",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_memory_revision_owner",
        "memory_revisions",
        "memories",
        ["user_id", "memory_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("memory_revisions_user_id_fkey"), "memory_revisions", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_message_assets_asset_id_owner",
        "message_assets",
        "assets",
        ["user_id", "asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_message_assets_message_id_owner",
        "message_assets",
        "messages",
        ["user_id", "message_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("message_assets_user_id_fkey"), "message_assets", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_message_voice_refs_message_id_owner",
        "message_voice_refs",
        "messages",
        ["user_id", "message_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_message_voice_refs_voice_id_owner",
        "message_voice_refs",
        "custom_voices",
        ["user_id", "voice_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        op.f("message_voice_refs_user_id_fkey"), "message_voice_refs", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_messages_owned_conversation",
        "messages",
        "conversations",
        ["user_id", "conversation_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(op.f("messages_user_id_fkey"), "messages", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        "fk_nightly_activity_actions_job_id_owner",
        "nightly_activity_actions",
        "jobs",
        ["user_id", "job_id"],
        ["user_id", "id"],
        ondelete="SET NULL (job_id)",
    )
    op.create_foreign_key(
        "fk_nightly_activity_actions_log_id_owner",
        "nightly_activity_actions",
        "nightly_activity_logs",
        ["user_id", "log_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("nightly_activity_actions_user_id_fkey"),
        "nightly_activity_actions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("nightly_activity_logs_user_id_fkey"),
        "nightly_activity_logs",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(op.f("personas_user_id_fkey"), "personas", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        "fk_post_publications_id_owner",
        "post_publications",
        "jobs",
        ["owner_id", "id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_post_publications_post_id_owner",
        "post_publications",
        "companion_entries",
        ["owner_id", "post_id"],
        ["user_id", "id"],
        ondelete="SET NULL (post_id)",
    )
    op.create_foreign_key(
        "fk_post_publications_video_job_id_owner",
        "post_publications",
        "video_gen_jobs",
        ["owner_id", "video_job_id"],
        ["owner_id", "id"],
        ondelete="SET NULL (video_job_id)",
    )
    op.create_foreign_key(
        "fk_prompt_submissions_conversation_id_owner",
        "prompt_submissions",
        "conversations",
        ["user_id", "conversation_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_prompt_submissions_replace_reply_message_id_owner",
        "prompt_submissions",
        "messages",
        ["user_id", "replace_reply_message_id"],
        ["user_id", "id"],
        ondelete="SET NULL (replace_reply_message_id)",
    )
    op.create_foreign_key(
        "fk_prompt_submissions_result_message_id_owner",
        "prompt_submissions",
        "messages",
        ["user_id", "result_message_id"],
        ["user_id", "id"],
        ondelete="SET NULL (result_message_id)",
    )
    op.create_foreign_key(
        op.f("prompt_submissions_user_id_fkey"), "prompt_submissions", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_provider_attempts_job_id_owner",
        "provider_attempts",
        "jobs",
        ["user_id", "job_id"],
        ["user_id", "id"],
        ondelete="SET NULL (job_id)",
    )
    op.create_foreign_key(
        op.f("provider_attempts_job_id_fkey"), "provider_attempts", "jobs", ["job_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        op.f("provider_attempts_lane_id_fkey"), "provider_attempts", "provider_lanes", ["lane_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("provider_attempts_user_id_fkey"), "provider_attempts", "users", ["user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_pursuit_evidence_event_id_owner",
        "pursuit_evidence",
        "life_events",
        ["user_id", "event_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_pursuit_evidence_pursuit_id_owner",
        "pursuit_evidence",
        "life_pursuits",
        ["user_id", "pursuit_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("pursuit_evidence_user_id_fkey"), "pursuit_evidence", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        op.f("remote_pairings_user_id_fkey"), "remote_pairings", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        op.f("remote_sessions_user_id_fkey"), "remote_sessions", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_scene_jobs_id_owner", "scene_jobs", "jobs", ["owner_id", "id"], ["user_id", "id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_scene_jobs_scene_id_owner",
        "scene_jobs",
        "companion_scenes",
        ["owner_id", "scene_id"],
        ["user_id", "id"],
        ondelete="SET NULL (scene_id)",
    )
    op.create_foreign_key(
        "fk_submission_messages_message_id_owner",
        "submission_messages",
        "messages",
        ["user_id", "message_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_submission_messages_submission_id_owner",
        "submission_messages",
        "prompt_submissions",
        ["user_id", "submission_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("submission_messages_user_id_fkey"),
        "submission_messages",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("user_model_configs_user_id_fkey"), "user_model_configs", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        op.f("user_settings_user_id_fkey"), "user_settings", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        op.f("user_social_accounts_user_id_fkey"),
        "user_social_accounts",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_video_gen_jobs_conversation_id_owner",
        "video_gen_jobs",
        "conversations",
        ["owner_id", "conversation_id"],
        ["user_id", "id"],
        ondelete="SET NULL (conversation_id)",
    )
    op.create_foreign_key(
        "fk_video_gen_jobs_id_owner",
        "video_gen_jobs",
        "jobs",
        ["owner_id", "id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_video_gen_jobs_reply_message_id_owner",
        "video_gen_jobs",
        "messages",
        ["owner_id", "reply_message_id"],
        ["user_id", "id"],
        ondelete="SET NULL (reply_message_id)",
    )
    op.create_foreign_key(
        "fk_video_gen_jobs_source_message_id_owner",
        "video_gen_jobs",
        "messages",
        ["owner_id", "source_message_id"],
        ["user_id", "id"],
        ondelete="SET NULL (source_message_id)",
    )
    op.create_foreign_key(
        "fk_visual_item_current_version",
        "visual_items",
        "visual_versions",
        ["id", "current_version_id"],
        ["visual_id", "id"],
        ondelete="SET NULL (current_version_id)",
        initially="DEFERRED",
        deferrable=True,
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_visual_items_action_id_owner",
        "visual_items",
        "action_definitions",
        ["user_id", "action_id"],
        ["user_id", "id"],
        ondelete="SET NULL (action_id)",
    )
    op.create_foreign_key(
        "fk_visual_items_outfit_id_owner",
        "visual_items",
        "companion_outfits",
        ["user_id", "outfit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (outfit_id)",
    )
    op.create_foreign_key(
        "fk_visual_items_scene_id_owner",
        "visual_items",
        "companion_scenes",
        ["user_id", "scene_id"],
        ["user_id", "id"],
        ondelete="SET NULL (scene_id)",
    )
    op.create_foreign_key(
        op.f("visual_items_user_id_fkey"), "visual_items", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_visual_job_image_pair",
        "visual_jobs",
        "visual_versions",
        ["result_image_id", "result_image_version_id"],
        ["visual_id", "id"],
        ondelete="SET NULL",
        initially="DEFERRED",
        deferrable=True,
        match="FULL",
    )
    op.create_foreign_key(
        "fk_visual_job_result_pair",
        "visual_jobs",
        "visual_versions",
        ["result_visual_id", "result_version_id"],
        ["visual_id", "id"],
        ondelete="SET NULL",
        initially="DEFERRED",
        deferrable=True,
        match="FULL",
    )
    op.create_foreign_key(
        "fk_visual_jobs_action_id_owner",
        "visual_jobs",
        "action_definitions",
        ["owner_id", "action_id"],
        ["user_id", "id"],
        ondelete="SET NULL (action_id)",
    )
    op.create_foreign_key(
        "fk_visual_jobs_id_owner", "visual_jobs", "jobs", ["owner_id", "id"], ["user_id", "id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_visual_jobs_outfit_id_owner",
        "visual_jobs",
        "companion_outfits",
        ["owner_id", "outfit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (outfit_id)",
    )
    op.create_foreign_key(
        "fk_visual_jobs_result_image_id_owner",
        "visual_jobs",
        "visual_items",
        ["owner_id", "result_image_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_visual_jobs_result_visual_id_owner",
        "visual_jobs",
        "visual_items",
        ["owner_id", "result_visual_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_visual_jobs_scene_id_owner",
        "visual_jobs",
        "companion_scenes",
        ["owner_id", "scene_id"],
        ["user_id", "id"],
        ondelete="SET NULL (scene_id)",
    )
    op.create_foreign_key(
        "fk_visual_jobs_target_visual_id_owner",
        "visual_jobs",
        "visual_items",
        ["owner_id", "target_visual_id"],
        ["user_id", "id"],
        ondelete="SET NULL (target_visual_id)",
    )
    op.create_foreign_key(
        "fk_playback_version_pair",
        "visual_playbacks",
        "visual_versions",
        ["visual_id", "version_id"],
        ["visual_id", "id"],
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_visual_playbacks_visual_id_owner",
        "visual_playbacks",
        "visual_items",
        ["user_id", "visual_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("visual_playbacks_user_id_fkey"), "visual_playbacks", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_visual_version_assets_asset_id_owner",
        "visual_version_assets",
        "assets",
        ["user_id", "asset_id"],
        ["user_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_visual_version_assets_version_id_owner",
        "visual_version_assets",
        "visual_versions",
        ["user_id", "version_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("visual_version_assets_user_id_fkey"),
        "visual_version_assets",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_visual_version_input_pair",
        "visual_versions",
        "visual_versions",
        ["input_image_id", "input_image_version_id"],
        ["visual_id", "id"],
        ondelete="SET NULL",
        initially="DEFERRED",
        deferrable=True,
        match="FULL",
    )
    op.create_foreign_key(
        "fk_visual_versions_action_id_owner",
        "visual_versions",
        "action_definitions",
        ["user_id", "action_id"],
        ["user_id", "id"],
        ondelete="SET NULL (action_id)",
    )
    op.create_foreign_key(
        "fk_visual_versions_input_image_id_owner",
        "visual_versions",
        "visual_items",
        ["user_id", "input_image_id"],
        ["user_id", "id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_visual_versions_outfit_id_owner",
        "visual_versions",
        "companion_outfits",
        ["user_id", "outfit_id"],
        ["user_id", "id"],
        ondelete="SET NULL (outfit_id)",
    )
    op.create_foreign_key(
        "fk_visual_versions_scene_id_owner",
        "visual_versions",
        "companion_scenes",
        ["user_id", "scene_id"],
        ["user_id", "id"],
        ondelete="SET NULL (scene_id)",
    )
    op.create_foreign_key(
        "fk_visual_versions_visual_id_owner",
        "visual_versions",
        "visual_items",
        ["user_id", "visual_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("visual_versions_user_id_fkey"), "visual_versions", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(op.f("ws_events_user_id_fkey"), "ws_events", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_index(
        op.f("ix_action_definitions_normalized_name"), "action_definitions", ["normalized_name"], unique=False
    )
    op.create_index(op.f("ix_action_definitions_status"), "action_definitions", ["status"], unique=False)
    op.create_index(op.f("ix_action_definitions_user_id"), "action_definitions", ["user_id"], unique=False)
    op.create_index(op.f("ix_admin_sessions_created_at"), "admin_sessions", ["created_at"], unique=False)
    op.create_index(op.f("ix_admin_sessions_is_active"), "admin_sessions", ["is_active"], unique=False)
    op.create_index(op.f("ix_admin_sessions_username"), "admin_sessions", ["username"], unique=False)
    op.create_index(
        op.f("ix_appearance_assets_analysis_job_id"), "appearance_assets", ["analysis_job_id"], unique=False
    )
    op.create_index(op.f("ix_appearance_assets_asset_id"), "appearance_assets", ["asset_id"], unique=False)
    op.create_index(op.f("ix_appearance_assets_avatar_id"), "appearance_assets", ["avatar_id"], unique=False)
    op.create_index(op.f("ix_appearance_assets_candidate_id"), "appearance_assets", ["candidate_id"], unique=False)
    op.create_index(op.f("ix_appearance_assets_outfit_id"), "appearance_assets", ["outfit_id"], unique=False)
    op.create_index(op.f("ix_appearance_assets_user_id"), "appearance_assets", ["user_id"], unique=False)
    op.create_index(
        "ix_assets_cleanup", "assets", ["not_before"], unique=False, postgresql_where=sa.text("state = 'cleanup'")
    )
    op.create_index(op.f("ix_assets_user_id"), "assets", ["user_id"], unique=False)
    op.create_index(op.f("ix_avatar_assets_active"), "avatar_assets", ["active"], unique=False)
    op.create_index(op.f("ix_avatar_assets_user_id"), "avatar_assets", ["user_id"], unique=False)
    op.create_index(
        "uq_avatar_assets_one_active", "avatar_assets", ["user_id"], unique=True, postgresql_where=sa.text("active")
    )
    op.create_index(
        op.f("ix_character_analysis_jobs_avatar_id"), "character_analysis_jobs", ["avatar_id"], unique=False
    )
    op.create_index(
        "ix_entries_timeline",
        "companion_entries",
        ["user_id", "kind", sa.literal_column("published_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_entries_unread",
        "companion_entries",
        ["user_id", "kind"],
        unique=False,
        postgresql_where=sa.text("is_read IS FALSE"),
    )
    op.create_index(
        "uq_entries_diary_date",
        "companion_entries",
        ["user_id", "activity_date"],
        unique=True,
        postgresql_where=sa.text("kind = 'diary'"),
    )
    op.create_index(
        op.f("ix_companion_fullbody_candidates_avatar_id"), "companion_fullbody_candidates", ["avatar_id"], unique=False
    )
    op.create_index(
        op.f("ix_companion_fullbody_candidates_status"), "companion_fullbody_candidates", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_companion_fullbody_candidates_user_id"), "companion_fullbody_candidates", ["user_id"], unique=False
    )
    op.create_index(op.f("ix_companion_intents_expires_at"), "companion_intents", ["expires_at"], unique=False)
    op.create_index(
        "ix_companion_intents_source",
        "companion_intents",
        ["user_id", "source_kind", "cron_job_id", "status"],
        unique=False,
    )
    op.create_index(op.f("ix_companion_intents_status"), "companion_intents", ["status"], unique=False)
    op.create_index(op.f("ix_companion_intents_user_id"), "companion_intents", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_intents_wake_at"), "companion_intents", ["wake_at"], unique=False)
    op.create_index(op.f("ix_companion_outfits_status"), "companion_outfits", ["status"], unique=False)
    op.create_index(op.f("ix_companion_outfits_user_id"), "companion_outfits", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_scenes_status"), "companion_scenes", ["status"], unique=False)
    op.create_index(op.f("ix_companion_scenes_user_id"), "companion_scenes", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_topic_batches_user_id"), "companion_topic_batches", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_companion_topic_feedback_topic_id"), "companion_topic_feedback", ["topic_id"], unique=False
    )
    op.create_index(op.f("ix_companion_topic_feedback_user_id"), "companion_topic_feedback", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_companion_topic_usages_message_id"), "companion_topic_usages", ["message_id"], unique=False
    )
    op.create_index(op.f("ix_companion_topic_usages_topic_id"), "companion_topic_usages", ["topic_id"], unique=False)
    op.create_index(op.f("ix_companion_topic_usages_user_id"), "companion_topic_usages", ["user_id"], unique=False)
    op.create_index("ix_companion_topics_batch_id", "companion_topics", ["batch_id"], unique=False)
    op.create_index(op.f("ix_companion_topics_user_id"), "companion_topics", ["user_id"], unique=False)
    op.create_index(op.f("ix_conversations_forked_from_id"), "conversations", ["forked_from_id"], unique=False)
    op.create_index(op.f("ix_conversations_parent_id"), "conversations", ["parent_id"], unique=False)
    op.create_index(op.f("ix_conversations_system_preset_id"), "conversations", ["system_preset_id"], unique=False)
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)
    op.create_index(
        "ix_conversations_user_message_count",
        "conversations",
        ["user_id", sa.literal_column("visible_message_count DESC"), "id"],
        unique=False,
    )
    op.create_index(
        "uq_conversations_user_preset",
        "conversations",
        ["user_id", "system_preset_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'special' AND system_preset_id IS NOT NULL"),
    )
    op.create_index(op.f("ix_cron_jobs_conversation_id"), "cron_jobs", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_cron_jobs_expires_at"), "cron_jobs", ["expires_at"], unique=False)
    op.create_index(op.f("ix_cron_jobs_name"), "cron_jobs", ["name"], unique=False)
    op.create_index(op.f("ix_cron_jobs_next_run_at"), "cron_jobs", ["next_run_at"], unique=False)
    op.create_index(op.f("ix_cron_jobs_user_id"), "cron_jobs", ["user_id"], unique=False)
    op.create_index(op.f("ix_custom_voices_expires_at"), "custom_voices", ["expires_at"], unique=False)
    op.create_index(op.f("ix_custom_voices_user_id"), "custom_voices", ["user_id"], unique=False)
    op.create_index(
        "uq_current_custom_voice",
        "custom_voices",
        ["user_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index("uq_custom_voice_token", "custom_voices", ["user_id", "token"], unique=True)
    op.create_index("ix_entry_assets_asset", "entry_assets", ["asset_id"], unique=False)
    op.create_index("ix_entry_comments_order", "entry_comments", ["post_id", "created_at", "id"], unique=False)
    op.create_index(
        "ix_entry_comments_pending",
        "entry_comments",
        ["user_id", "post_id"],
        unique=False,
        postgresql_where=sa.text("role = 'user' AND reply_status IN ('pending', 'running')"),
    )
    op.create_index("ix_entry_links_target", "entry_links", ["target_id"], unique=False)
    op.create_index(op.f("ix_job_assets_asset_id"), "job_assets", ["asset_id"], unique=False)
    op.create_index(op.f("ix_job_assets_job_id"), "job_assets", ["job_id"], unique=False)
    op.create_index(op.f("ix_job_requests_job_id"), "job_requests", ["job_id"], unique=False)
    op.create_index("ix_jobs_recovery", "jobs", ["kind", "state", "not_before"], unique=False)
    op.create_index(op.f("ix_jobs_user_id"), "jobs", ["user_id"], unique=False)
    op.create_index("ix_jobs_user_state_id", "jobs", ["user_id", "state", "id"], unique=False)
    op.create_index(op.f("ix_login_records_is_active"), "login_records", ["is_active"], unique=False)
    op.create_index(op.f("ix_login_records_login_at"), "login_records", ["login_at"], unique=False)
    op.create_index(op.f("ix_login_records_user_id"), "login_records", ["user_id"], unique=False)
    op.create_index(
        "ix_memories_content_trgm",
        "memories",
        ["content"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_memories_context_trgm",
        "memories",
        ["context"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"context": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_memories_scope_kind", "memories", ["user_id", "system_preset_id", "kind", "status"], unique=False
    )
    op.create_index(
        "ix_memories_scope_updated",
        "memories",
        ["user_id", "system_preset_id", sa.literal_column("updated_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index("ix_memory_evidence_memory", "memory_evidence", ["memory_id"], unique=False)
    op.create_index(
        "ix_memory_fingerprints_suppressed",
        "memory_fingerprints",
        ["user_id", "system_preset_id", "fingerprint"],
        unique=False,
        postgresql_where=sa.text("suppressed"),
    )
    op.create_index("ix_memory_revisions_memory", "memory_revisions", ["memory_id", "content_version"], unique=False)
    op.create_index(op.f("ix_message_assets_asset_id"), "message_assets", ["asset_id"], unique=False)
    op.create_index(op.f("ix_message_assets_message_id"), "message_assets", ["message_id"], unique=False)
    op.create_index(op.f("ix_message_assets_user_id"), "message_assets", ["user_id"], unique=False)
    op.create_index(op.f("ix_message_voice_refs_message_id"), "message_voice_refs", ["message_id"], unique=False)
    op.create_index(op.f("ix_message_voice_refs_user_id"), "message_voice_refs", ["user_id"], unique=False)
    op.create_index(op.f("ix_message_voice_refs_voice_id"), "message_voice_refs", ["voice_id"], unique=False)
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)
    op.create_index("ix_messages_conversation_order", "messages", ["conversation_id", "id"], unique=False)
    op.create_index(
        "ix_messages_conversation_user_preview",
        "messages",
        ["conversation_id", "id"],
        unique=False,
        postgresql_where=sa.text("role = 'user' AND content IS NOT NULL AND content != ''"),
    )
    op.create_index(op.f("ix_messages_subtype"), "messages", ["subtype"], unique=False)
    op.create_index(op.f("ix_messages_user_id"), "messages", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_nightly_activity_actions_capability"), "nightly_activity_actions", ["capability"], unique=False
    )
    op.create_index(op.f("ix_nightly_activity_actions_job_id"), "nightly_activity_actions", ["job_id"], unique=False)
    op.create_index(op.f("ix_nightly_activity_actions_log_id"), "nightly_activity_actions", ["log_id"], unique=False)
    op.create_index(op.f("ix_nightly_activity_actions_status"), "nightly_activity_actions", ["status"], unique=False)
    op.create_index(
        op.f("ix_nightly_activity_actions_target_date"), "nightly_activity_actions", ["target_date"], unique=False
    )
    op.create_index(op.f("ix_nightly_activity_actions_user_id"), "nightly_activity_actions", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_nightly_activity_logs_target_date"), "nightly_activity_logs", ["target_date"], unique=False
    )
    op.create_index(op.f("ix_nightly_activity_logs_user_id"), "nightly_activity_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_personas_is_complete"), "personas", ["is_complete"], unique=False)
    op.create_index(op.f("ix_personas_is_portrait_confirmed"), "personas", ["is_portrait_confirmed"], unique=False)
    op.create_index(op.f("ix_post_publications_reserved_at"), "post_publications", ["reserved_at"], unique=False)
    op.create_index(op.f("ix_post_publications_video_job_id"), "post_publications", ["video_job_id"], unique=False)
    op.create_index(
        op.f("ix_prompt_submissions_conversation_id"), "prompt_submissions", ["conversation_id"], unique=False
    )
    op.create_index(op.f("ix_prompt_submissions_user_id"), "prompt_submissions", ["user_id"], unique=False)
    op.create_index(op.f("ix_provider_attempts_job_id"), "provider_attempts", ["job_id"], unique=False)
    op.create_index(op.f("ix_provider_attempts_state"), "provider_attempts", ["state"], unique=False)
    op.create_index(op.f("ix_provider_attempts_user_id"), "provider_attempts", ["user_id"], unique=False)
    op.create_index("ix_video_queue_fifo", "provider_attempts", ["lane_id", "state", "enqueued_at", "id"], unique=False)
    op.create_index("ix_video_queue_owner", "provider_attempts", ["job_id", "generation_id"], unique=False)
    op.create_index(op.f("ix_pursuit_evidence_event_id"), "pursuit_evidence", ["event_id"], unique=False)
    op.create_index(op.f("ix_pursuit_evidence_pursuit_id"), "pursuit_evidence", ["pursuit_id"], unique=False)
    op.create_index(op.f("ix_pursuit_evidence_user_id"), "pursuit_evidence", ["user_id"], unique=False)
    op.create_index(op.f("ix_remote_pairings_user_id"), "remote_pairings", ["user_id"], unique=False)
    op.create_index(op.f("ix_remote_sessions_expires_at"), "remote_sessions", ["expires_at"], unique=False)
    op.create_index(op.f("ix_remote_sessions_user_id"), "remote_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_scene_jobs_scene_id"), "scene_jobs", ["scene_id"], unique=False)
    op.create_index(op.f("ix_submission_messages_message_id"), "submission_messages", ["message_id"], unique=False)
    op.create_index(
        op.f("ix_submission_messages_submission_id"), "submission_messages", ["submission_id"], unique=False
    )
    op.create_index(op.f("ix_submission_messages_user_id"), "submission_messages", ["user_id"], unique=False)
    op.create_index(op.f("ix_system_settings_setting_key"), "system_settings", ["setting_key"], unique=True)
    op.create_index(op.f("ix_update_versions_is_active"), "update_versions", ["is_active"], unique=False)
    op.create_index(op.f("ix_update_versions_version"), "update_versions", ["version"], unique=True)
    op.create_index(op.f("ix_user_settings_setting_key"), "user_settings", ["setting_key"], unique=False)
    op.create_index(op.f("ix_user_settings_user_id"), "user_settings", ["user_id"], unique=False)
    op.create_index(op.f("ix_users_activation_token_hash"), "users", ["activation_token_hash"], unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_index(
        op.f("ix_video_gen_jobs_cleanup_requested_at"), "video_gen_jobs", ["cleanup_requested_at"], unique=False
    )
    op.create_index(op.f("ix_video_gen_jobs_conversation_id"), "video_gen_jobs", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_provider_task_id"), "video_gen_jobs", ["provider_task_id"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_reply_message_id"), "video_gen_jobs", ["reply_message_id"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_source_message_id"), "video_gen_jobs", ["source_message_id"], unique=False)
    op.create_index(op.f("ix_visual_items_action_id"), "visual_items", ["action_id"], unique=False)
    op.create_index(
        "ix_visual_items_catalog",
        "visual_items",
        ["user_id", "status", "media_kind", "asset_kind", "id"],
        unique=False,
    )
    op.create_index(op.f("ix_visual_items_outfit_id"), "visual_items", ["outfit_id"], unique=False)
    op.create_index(op.f("ix_visual_items_scene_id"), "visual_items", ["scene_id"], unique=False)
    op.create_index(op.f("ix_visual_items_user_id"), "visual_items", ["user_id"], unique=False)
    op.create_index(
        "uq_visual_items_image_content",
        "visual_items",
        ["user_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("media_kind = 'image'"),
    )
    op.create_index(op.f("ix_visual_jobs_target_visual_id"), "visual_jobs", ["target_visual_id"], unique=False)
    op.create_index(op.f("ix_visual_playbacks_user_id"), "visual_playbacks", ["user_id"], unique=False)
    op.create_index(op.f("ix_visual_playbacks_version_id"), "visual_playbacks", ["version_id"], unique=False)
    op.create_index(op.f("ix_visual_playbacks_visual_id"), "visual_playbacks", ["visual_id"], unique=False)
    op.create_index(op.f("ix_visual_version_assets_asset_id"), "visual_version_assets", ["asset_id"], unique=False)
    op.create_index(op.f("ix_visual_version_assets_version_id"), "visual_version_assets", ["version_id"], unique=False)
    op.create_index(op.f("ix_visual_versions_user_id"), "visual_versions", ["user_id"], unique=False)
    op.create_index(op.f("ix_visual_versions_visual_id"), "visual_versions", ["visual_id"], unique=False)
    op.create_index(op.f("ix_ws_events_created_at"), "ws_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_ws_events_next_retry_at"), "ws_events", ["next_retry_at"], unique=False)
    op.create_index("ix_ws_events_poll", "ws_events", ["user_id", "status", "next_retry_at"], unique=False)
    op.create_index(op.f("ix_ws_events_status"), "ws_events", ["status"], unique=False)
    op.create_index(op.f("ix_ws_events_user_id"), "ws_events", ["user_id"], unique=False)
    op.execute(
        "CREATE OR REPLACE FUNCTION conversation_statistics_insert() RETURNS trigger\nLANGUAGE plpgsql AS $statistics$\nBEGIN\n    -- 先按会话顺序取锁，再用最新预设/父会话语义计算增量；与元数据重算串行。\n    PERFORM id FROM conversations WHERE id IN (SELECT conversation_id FROM new_rows) ORDER BY id FOR NO KEY UPDATE;\n    WITH batches AS (SELECT messages.conversation_id, sum(CASE WHEN (messages.role = 'user' AND conversations.system_preset_id = 'companion' AND conversations.parent_id IS NULL AND (messages.subtype IS NULL OR messages.subtype = '')) THEN (SELECT greatest(count(*), 1) AS greatest_1 \nFROM regexp_split_to_table(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_2.value ->> 'text'), '\n' ORDER BY anon_2.position) AS string_agg_1 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_2(value, position) \nWHERE (anon_2.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_2.value['text']) = 'string') ELSE messages.content END, '\\r?\\n(?:[ \\t]*\\r?\\n)+') AS anon_1(segment) \nWHERE btrim(anon_1.segment, ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '') WHEN (messages.role = 'user') THEN 1 WHEN (messages.role = 'assistant' AND messages.content_type = 'companion_reply') THEN CASE WHEN (jsonb_typeof(messages.reply_json['bubbles']) = 'array') THEN jsonb_array_length(messages.reply_json['bubbles']) ELSE 0 END WHEN (messages.role = 'assistant' AND (btrim(regexp_replace(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_3.value ->> 'text'), '\n' ORDER BY anon_3.position) AS string_agg_2 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_3(value, position) \nWHERE (anon_3.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_3.value['text']) = 'string') ELSE messages.content END, '(?<![A-Za-z0-9_])MEDIA:[^ \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*', '', 'g'), ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '' OR CASE WHEN (jsonb_typeof(messages.media_json) = 'array') THEN (messages.media_json @? 'strict $[*] ? (@.type() == \"object\")'::jsonpath) ELSE false END)) THEN 1 ELSE 0 END) * 1 AS visible_message_count, coalesce(sum(messages.prompt_tokens), 0) * 1 AS input_token_count, coalesce(sum(messages.completion_tokens), 0) * 1 AS output_token_count, (count(messages.id) FILTER (WHERE messages.tool_calls IS NOT NULL)) * 1 AS tool_call_count \nFROM new_rows AS messages JOIN conversations ON conversations.id = messages.conversation_id GROUP BY messages.conversation_id), delta AS (\n        SELECT conversation_id, sum(visible_message_count) AS visible_message_count, sum(input_token_count) AS input_token_count, sum(output_token_count) AS output_token_count, sum(tool_call_count) AS tool_call_count FROM batches GROUP BY conversation_id\n    )\n    UPDATE conversations AS target SET visible_message_count = target.visible_message_count + delta.visible_message_count, input_token_count = target.input_token_count + delta.input_token_count, output_token_count = target.output_token_count + delta.output_token_count, tool_call_count = target.tool_call_count + delta.tool_call_count\n    FROM delta WHERE target.id = delta.conversation_id AND (delta.visible_message_count != 0 OR delta.input_token_count != 0 OR delta.output_token_count != 0 OR delta.tool_call_count != 0);\n    RETURN NULL;\nEND\n$statistics$"
    )
    op.execute(
        "CREATE TRIGGER messages_statistics_insert AFTER INSERT ON messages REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION conversation_statistics_insert()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION conversation_statistics_update() RETURNS trigger\nLANGUAGE plpgsql AS $statistics$\nBEGIN\n    -- 先按会话顺序取锁，再用最新预设/父会话语义计算增量；与元数据重算串行。\n    PERFORM id FROM conversations WHERE id IN (SELECT conversation_id FROM new_rows UNION SELECT conversation_id FROM old_rows) ORDER BY id FOR NO KEY UPDATE;\n    WITH batches AS (SELECT messages.conversation_id, sum(CASE WHEN (messages.role = 'user' AND conversations.system_preset_id = 'companion' AND conversations.parent_id IS NULL AND (messages.subtype IS NULL OR messages.subtype = '')) THEN (SELECT greatest(count(*), 1) AS greatest_1 \nFROM regexp_split_to_table(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_2.value ->> 'text'), '\n' ORDER BY anon_2.position) AS string_agg_1 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_2(value, position) \nWHERE (anon_2.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_2.value['text']) = 'string') ELSE messages.content END, '\\r?\\n(?:[ \\t]*\\r?\\n)+') AS anon_1(segment) \nWHERE btrim(anon_1.segment, ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '') WHEN (messages.role = 'user') THEN 1 WHEN (messages.role = 'assistant' AND messages.content_type = 'companion_reply') THEN CASE WHEN (jsonb_typeof(messages.reply_json['bubbles']) = 'array') THEN jsonb_array_length(messages.reply_json['bubbles']) ELSE 0 END WHEN (messages.role = 'assistant' AND (btrim(regexp_replace(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_3.value ->> 'text'), '\n' ORDER BY anon_3.position) AS string_agg_2 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_3(value, position) \nWHERE (anon_3.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_3.value['text']) = 'string') ELSE messages.content END, '(?<![A-Za-z0-9_])MEDIA:[^ \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*', '', 'g'), ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '' OR CASE WHEN (jsonb_typeof(messages.media_json) = 'array') THEN (messages.media_json @? 'strict $[*] ? (@.type() == \"object\")'::jsonpath) ELSE false END)) THEN 1 ELSE 0 END) * 1 AS visible_message_count, coalesce(sum(messages.prompt_tokens), 0) * 1 AS input_token_count, coalesce(sum(messages.completion_tokens), 0) * 1 AS output_token_count, (count(messages.id) FILTER (WHERE messages.tool_calls IS NOT NULL)) * 1 AS tool_call_count \nFROM new_rows AS messages JOIN conversations ON conversations.id = messages.conversation_id LEFT OUTER JOIN old_rows AS previous ON previous.id = messages.id \nWHERE (messages.conversation_id, messages.role, messages.subtype, messages.content, messages.content_type, messages.prompt_tokens, messages.completion_tokens, messages.tool_calls, messages.media_json, messages.reply_json) IS DISTINCT FROM (previous.conversation_id, previous.role, previous.subtype, previous.content, previous.content_type, previous.prompt_tokens, previous.completion_tokens, previous.tool_calls, previous.media_json, previous.reply_json) GROUP BY messages.conversation_id UNION ALL SELECT messages.conversation_id, sum(CASE WHEN (messages.role = 'user' AND conversations.system_preset_id = 'companion' AND conversations.parent_id IS NULL AND (messages.subtype IS NULL OR messages.subtype = '')) THEN (SELECT greatest(count(*), 1) AS greatest_1 \nFROM regexp_split_to_table(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_2.value ->> 'text'), '\n' ORDER BY anon_2.position) AS string_agg_1 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_2(value, position) \nWHERE (anon_2.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_2.value['text']) = 'string') ELSE messages.content END, '\\r?\\n(?:[ \\t]*\\r?\\n)+') AS anon_1(segment) \nWHERE btrim(anon_1.segment, ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '') WHEN (messages.role = 'user') THEN 1 WHEN (messages.role = 'assistant' AND messages.content_type = 'companion_reply') THEN CASE WHEN (jsonb_typeof(messages.reply_json['bubbles']) = 'array') THEN jsonb_array_length(messages.reply_json['bubbles']) ELSE 0 END WHEN (messages.role = 'assistant' AND (btrim(regexp_replace(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_3.value ->> 'text'), '\n' ORDER BY anon_3.position) AS string_agg_2 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_3(value, position) \nWHERE (anon_3.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_3.value['text']) = 'string') ELSE messages.content END, '(?<![A-Za-z0-9_])MEDIA:[^ \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*', '', 'g'), ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '' OR CASE WHEN (jsonb_typeof(messages.media_json) = 'array') THEN (messages.media_json @? 'strict $[*] ? (@.type() == \"object\")'::jsonpath) ELSE false END)) THEN 1 ELSE 0 END) * -1 AS visible_message_count, coalesce(sum(messages.prompt_tokens), 0) * -1 AS input_token_count, coalesce(sum(messages.completion_tokens), 0) * -1 AS output_token_count, (count(messages.id) FILTER (WHERE messages.tool_calls IS NOT NULL)) * -1 AS tool_call_count \nFROM old_rows AS messages JOIN conversations ON conversations.id = messages.conversation_id LEFT OUTER JOIN new_rows AS previous ON previous.id = messages.id \nWHERE (messages.conversation_id, messages.role, messages.subtype, messages.content, messages.content_type, messages.prompt_tokens, messages.completion_tokens, messages.tool_calls, messages.media_json, messages.reply_json) IS DISTINCT FROM (previous.conversation_id, previous.role, previous.subtype, previous.content, previous.content_type, previous.prompt_tokens, previous.completion_tokens, previous.tool_calls, previous.media_json, previous.reply_json) GROUP BY messages.conversation_id), delta AS (\n        SELECT conversation_id, sum(visible_message_count) AS visible_message_count, sum(input_token_count) AS input_token_count, sum(output_token_count) AS output_token_count, sum(tool_call_count) AS tool_call_count FROM batches GROUP BY conversation_id\n    )\n    UPDATE conversations AS target SET visible_message_count = target.visible_message_count + delta.visible_message_count, input_token_count = target.input_token_count + delta.input_token_count, output_token_count = target.output_token_count + delta.output_token_count, tool_call_count = target.tool_call_count + delta.tool_call_count\n    FROM delta WHERE target.id = delta.conversation_id AND (delta.visible_message_count != 0 OR delta.input_token_count != 0 OR delta.output_token_count != 0 OR delta.tool_call_count != 0);\n    RETURN NULL;\nEND\n$statistics$"
    )
    op.execute(
        "CREATE TRIGGER messages_statistics_update AFTER UPDATE ON messages REFERENCING NEW TABLE AS new_rows OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION conversation_statistics_update()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION conversation_statistics_delete() RETURNS trigger\nLANGUAGE plpgsql AS $statistics$\nBEGIN\n    -- 先按会话顺序取锁，再用最新预设/父会话语义计算增量；与元数据重算串行。\n    PERFORM id FROM conversations WHERE id IN (SELECT conversation_id FROM old_rows) ORDER BY id FOR NO KEY UPDATE;\n    WITH batches AS (SELECT messages.conversation_id, sum(CASE WHEN (messages.role = 'user' AND conversations.system_preset_id = 'companion' AND conversations.parent_id IS NULL AND (messages.subtype IS NULL OR messages.subtype = '')) THEN (SELECT greatest(count(*), 1) AS greatest_1 \nFROM regexp_split_to_table(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_2.value ->> 'text'), '\n' ORDER BY anon_2.position) AS string_agg_1 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_2(value, position) \nWHERE (anon_2.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_2.value['text']) = 'string') ELSE messages.content END, '\\r?\\n(?:[ \\t]*\\r?\\n)+') AS anon_1(segment) \nWHERE btrim(anon_1.segment, ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '') WHEN (messages.role = 'user') THEN 1 WHEN (messages.role = 'assistant' AND messages.content_type = 'companion_reply') THEN CASE WHEN (jsonb_typeof(messages.reply_json['bubbles']) = 'array') THEN jsonb_array_length(messages.reply_json['bubbles']) ELSE 0 END WHEN (messages.role = 'assistant' AND (btrim(regexp_replace(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_3.value ->> 'text'), '\n' ORDER BY anon_3.position) AS string_agg_2 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_3(value, position) \nWHERE (anon_3.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_3.value['text']) = 'string') ELSE messages.content END, '(?<![A-Za-z0-9_])MEDIA:[^ \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*', '', 'g'), ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '' OR CASE WHEN (jsonb_typeof(messages.media_json) = 'array') THEN (messages.media_json @? 'strict $[*] ? (@.type() == \"object\")'::jsonpath) ELSE false END)) THEN 1 ELSE 0 END) * -1 AS visible_message_count, coalesce(sum(messages.prompt_tokens), 0) * -1 AS input_token_count, coalesce(sum(messages.completion_tokens), 0) * -1 AS output_token_count, (count(messages.id) FILTER (WHERE messages.tool_calls IS NOT NULL)) * -1 AS tool_call_count \nFROM old_rows AS messages JOIN conversations ON conversations.id = messages.conversation_id GROUP BY messages.conversation_id), delta AS (\n        SELECT conversation_id, sum(visible_message_count) AS visible_message_count, sum(input_token_count) AS input_token_count, sum(output_token_count) AS output_token_count, sum(tool_call_count) AS tool_call_count FROM batches GROUP BY conversation_id\n    )\n    UPDATE conversations AS target SET visible_message_count = target.visible_message_count + delta.visible_message_count, input_token_count = target.input_token_count + delta.input_token_count, output_token_count = target.output_token_count + delta.output_token_count, tool_call_count = target.tool_call_count + delta.tool_call_count\n    FROM delta WHERE target.id = delta.conversation_id AND (delta.visible_message_count != 0 OR delta.input_token_count != 0 OR delta.output_token_count != 0 OR delta.tool_call_count != 0);\n    RETURN NULL;\nEND\n$statistics$"
    )
    op.execute(
        "CREATE TRIGGER messages_statistics_delete AFTER DELETE ON messages REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION conversation_statistics_delete()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION conversation_statistics_truncate() RETURNS trigger\nLANGUAGE plpgsql AS $statistics$\nBEGIN\n    UPDATE conversations SET visible_message_count = 0, input_token_count = 0, output_token_count = 0, tool_call_count = 0 WHERE visible_message_count != 0 OR input_token_count != 0 OR output_token_count != 0 OR tool_call_count != 0;\n    RETURN NULL;\nEND\n$statistics$"
    )
    op.execute(
        "CREATE TRIGGER messages_statistics_truncate AFTER TRUNCATE ON messages FOR EACH STATEMENT EXECUTE FUNCTION conversation_statistics_truncate()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION conversation_statistics_context() RETURNS trigger\nLANGUAGE plpgsql AS $statistics$\nBEGIN\n    IF NOT EXISTS (SELECT current.id FROM new_conversations AS current JOIN old_conversations AS previous USING (id)\n        WHERE (current.parent_id, current.system_preset_id) IS DISTINCT FROM (previous.parent_id, previous.system_preset_id)) THEN RETURN NULL; END IF;\n    WITH changed AS (SELECT current.id FROM new_conversations AS current JOIN old_conversations AS previous USING (id)\n        WHERE (current.parent_id, current.system_preset_id) IS DISTINCT FROM (previous.parent_id, previous.system_preset_id)), totals AS (SELECT messages.conversation_id, sum(CASE WHEN (messages.role = 'user' AND conversations.system_preset_id = 'companion' AND conversations.parent_id IS NULL AND (messages.subtype IS NULL OR messages.subtype = '')) THEN (SELECT greatest(count(*), 1) AS greatest_1 \nFROM regexp_split_to_table(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_2.value ->> 'text'), '\n' ORDER BY anon_2.position) AS string_agg_1 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_2(value, position) \nWHERE (anon_2.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_2.value['text']) = 'string') ELSE messages.content END, '\\r?\\n(?:[ \\t]*\\r?\\n)+') AS anon_1(segment) \nWHERE btrim(anon_1.segment, ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '') WHEN (messages.role = 'user') THEN 1 WHEN (messages.role = 'assistant' AND messages.content_type = 'companion_reply') THEN CASE WHEN (jsonb_typeof(messages.reply_json['bubbles']) = 'array') THEN jsonb_array_length(messages.reply_json['bubbles']) ELSE 0 END WHEN (messages.role = 'assistant' AND (btrim(regexp_replace(CASE WHEN (messages.content_type = 'multimodal_v1' AND (messages.content IS JSON ARRAY) AND pg_input_is_valid(messages.content, 'jsonb')) THEN (SELECT string_agg((anon_3.value ->> 'text'), '\n' ORDER BY anon_3.position) AS string_agg_2 \nFROM jsonb_array_elements(CAST(messages.content AS JSONB)) WITH ORDINALITY AS anon_3(value, position) \nWHERE (anon_3.value ->> 'type') = 'input_text' AND jsonb_typeof(anon_3.value['text']) = 'string') ELSE messages.content END, '(?<![A-Za-z0-9_])MEDIA:[^ \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*', '', 'g'), ' \t\r\n\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff') != '' OR CASE WHEN (jsonb_typeof(messages.media_json) = 'array') THEN (messages.media_json @? 'strict $[*] ? (@.type() == \"object\")'::jsonpath) ELSE false END)) THEN 1 ELSE 0 END) * 1 AS visible_message_count, coalesce(sum(messages.prompt_tokens), 0) * 1 AS input_token_count, coalesce(sum(messages.completion_tokens), 0) * 1 AS output_token_count, (count(messages.id) FILTER (WHERE messages.tool_calls IS NOT NULL)) * 1 AS tool_call_count \nFROM messages AS messages JOIN conversations ON conversations.id = messages.conversation_id \nWHERE messages.conversation_id IN (SELECT changed.id \nFROM changed) GROUP BY messages.conversation_id)\n    UPDATE conversations AS target SET visible_message_count = coalesce(totals.visible_message_count, 0), input_token_count = coalesce(totals.input_token_count, 0), output_token_count = coalesce(totals.output_token_count, 0), tool_call_count = coalesce(totals.tool_call_count, 0)\n    FROM changed LEFT JOIN totals ON totals.conversation_id = changed.id\n    WHERE target.id = changed.id;\n    RETURN NULL;\nEND\n$statistics$"
    )
    op.execute(
        "CREATE TRIGGER conversations_statistics_context AFTER UPDATE ON conversations REFERENCING OLD TABLE AS old_conversations NEW TABLE AS new_conversations FOR EACH STATEMENT EXECUTE FUNCTION conversation_statistics_context()"
    )
    op.execute("""
CREATE FUNCTION notify_ws_event() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('ws_events_channel', 'wakeup');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")
    op.execute("""
CREATE TRIGGER ws_event_notify_trigger
AFTER INSERT ON ws_events
FOR EACH STATEMENT EXECUTE FUNCTION notify_ws_event();
""")


def downgrade() -> None:
    op.drop_constraint(op.f("life_action_admissions_user_id_fkey"), "life_action_admissions", type_="foreignkey")
    op.drop_constraint("fk_life_events_result_action_id_owner", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_events_result_scene_id_owner", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_events_result_outfit_id_owner", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_pursuits_previous_pursuit_id_owner", "life_pursuits", type_="foreignkey")
    op.drop_constraint("fk_life_states_activity_step_id_owner", "life_states", type_="foreignkey")
    op.drop_constraint("fk_life_steps_result_outfit_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_steps_business_action_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_steps_result_action_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_steps_result_scene_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_outfit_creation_jobs_result_outfit_id_owner", "outfit_creation_jobs", type_="foreignkey")
    op.drop_constraint("fk_outfit_creation_jobs_id_owner", "outfit_creation_jobs", type_="foreignkey")
    op.execute("DROP TRIGGER IF EXISTS ws_event_notify_trigger ON ws_events")
    op.execute("DROP FUNCTION IF EXISTS notify_ws_event()")
    op.execute("DROP TRIGGER IF EXISTS messages_statistics_insert ON messages")
    op.execute("DROP TRIGGER IF EXISTS messages_statistics_update ON messages")
    op.execute("DROP TRIGGER IF EXISTS messages_statistics_delete ON messages")
    op.execute("DROP TRIGGER IF EXISTS messages_statistics_truncate ON messages")
    op.execute("DROP TRIGGER IF EXISTS conversations_statistics_context ON conversations")
    op.execute("DROP FUNCTION IF EXISTS conversation_statistics_insert()")
    op.execute("DROP FUNCTION IF EXISTS conversation_statistics_update()")
    op.execute("DROP FUNCTION IF EXISTS conversation_statistics_delete()")
    op.execute("DROP FUNCTION IF EXISTS conversation_statistics_truncate()")
    op.execute("DROP FUNCTION IF EXISTS conversation_statistics_context()")
    op.drop_constraint(op.f("ws_events_user_id_fkey"), "ws_events", type_="foreignkey")
    op.drop_constraint("fk_visual_version_input_pair", "visual_versions", type_="foreignkey")
    op.drop_constraint("fk_visual_versions_action_id_owner", "visual_versions", type_="foreignkey")
    op.drop_constraint("fk_visual_versions_input_image_id_owner", "visual_versions", type_="foreignkey")
    op.drop_constraint("fk_visual_versions_outfit_id_owner", "visual_versions", type_="foreignkey")
    op.drop_constraint("fk_visual_versions_scene_id_owner", "visual_versions", type_="foreignkey")
    op.drop_constraint("fk_visual_versions_visual_id_owner", "visual_versions", type_="foreignkey")
    op.drop_constraint(op.f("visual_versions_user_id_fkey"), "visual_versions", type_="foreignkey")
    op.drop_constraint("fk_visual_version_assets_asset_id_owner", "visual_version_assets", type_="foreignkey")
    op.drop_constraint("fk_visual_version_assets_version_id_owner", "visual_version_assets", type_="foreignkey")
    op.drop_constraint(op.f("visual_version_assets_user_id_fkey"), "visual_version_assets", type_="foreignkey")
    op.drop_constraint("fk_presentation_current_pair", "visual_presentations", type_="foreignkey")
    op.drop_constraint("fk_presentation_loop_pair", "visual_presentations", type_="foreignkey")
    op.drop_constraint("fk_visual_presentations_current_visual_id_owner", "visual_presentations", type_="foreignkey")
    op.drop_constraint("fk_visual_presentations_loop_visual_id_owner", "visual_presentations", type_="foreignkey")
    op.drop_constraint(op.f("visual_presentations_user_id_fkey"), "visual_presentations", type_="foreignkey")
    op.drop_constraint("fk_playback_version_pair", "visual_playbacks", type_="foreignkey")
    op.drop_constraint("fk_visual_playbacks_visual_id_owner", "visual_playbacks", type_="foreignkey")
    op.drop_constraint(op.f("visual_playbacks_user_id_fkey"), "visual_playbacks", type_="foreignkey")
    op.drop_constraint("fk_visual_job_image_pair", "visual_jobs", type_="foreignkey")
    op.drop_constraint("fk_visual_job_result_pair", "visual_jobs", type_="foreignkey")
    op.drop_constraint("fk_visual_jobs_action_id_owner", "visual_jobs", type_="foreignkey")
    op.drop_constraint("fk_visual_jobs_id_owner", "visual_jobs", type_="foreignkey")
    op.drop_constraint("fk_visual_jobs_outfit_id_owner", "visual_jobs", type_="foreignkey")
    op.drop_constraint("fk_visual_jobs_result_image_id_owner", "visual_jobs", type_="foreignkey")
    op.drop_constraint("fk_visual_jobs_result_visual_id_owner", "visual_jobs", type_="foreignkey")
    op.drop_constraint("fk_visual_jobs_scene_id_owner", "visual_jobs", type_="foreignkey")
    op.drop_constraint("fk_visual_jobs_target_visual_id_owner", "visual_jobs", type_="foreignkey")
    op.drop_constraint("fk_visual_item_current_version", "visual_items", type_="foreignkey")
    op.drop_constraint("fk_visual_items_action_id_owner", "visual_items", type_="foreignkey")
    op.drop_constraint("fk_visual_items_outfit_id_owner", "visual_items", type_="foreignkey")
    op.drop_constraint("fk_visual_items_scene_id_owner", "visual_items", type_="foreignkey")
    op.drop_constraint(op.f("visual_items_user_id_fkey"), "visual_items", type_="foreignkey")
    op.drop_constraint("fk_video_gen_jobs_conversation_id_owner", "video_gen_jobs", type_="foreignkey")
    op.drop_constraint("fk_video_gen_jobs_id_owner", "video_gen_jobs", type_="foreignkey")
    op.drop_constraint("fk_video_gen_jobs_reply_message_id_owner", "video_gen_jobs", type_="foreignkey")
    op.drop_constraint("fk_video_gen_jobs_source_message_id_owner", "video_gen_jobs", type_="foreignkey")
    op.drop_constraint(op.f("user_social_accounts_user_id_fkey"), "user_social_accounts", type_="foreignkey")
    op.drop_constraint(op.f("user_settings_user_id_fkey"), "user_settings", type_="foreignkey")
    op.drop_constraint(op.f("user_model_configs_user_id_fkey"), "user_model_configs", type_="foreignkey")
    op.drop_constraint("fk_submission_messages_message_id_owner", "submission_messages", type_="foreignkey")
    op.drop_constraint("fk_submission_messages_submission_id_owner", "submission_messages", type_="foreignkey")
    op.drop_constraint(op.f("submission_messages_user_id_fkey"), "submission_messages", type_="foreignkey")
    op.drop_constraint("fk_scene_jobs_id_owner", "scene_jobs", type_="foreignkey")
    op.drop_constraint("fk_scene_jobs_scene_id_owner", "scene_jobs", type_="foreignkey")
    op.drop_constraint(op.f("remote_sessions_user_id_fkey"), "remote_sessions", type_="foreignkey")
    op.drop_constraint(op.f("remote_pairings_user_id_fkey"), "remote_pairings", type_="foreignkey")
    op.drop_constraint("fk_pursuit_evidence_event_id_owner", "pursuit_evidence", type_="foreignkey")
    op.drop_constraint("fk_pursuit_evidence_pursuit_id_owner", "pursuit_evidence", type_="foreignkey")
    op.drop_constraint(op.f("pursuit_evidence_user_id_fkey"), "pursuit_evidence", type_="foreignkey")
    op.drop_constraint("fk_provider_attempts_job_id_owner", "provider_attempts", type_="foreignkey")
    op.drop_constraint(op.f("provider_attempts_job_id_fkey"), "provider_attempts", type_="foreignkey")
    op.drop_constraint(op.f("provider_attempts_lane_id_fkey"), "provider_attempts", type_="foreignkey")
    op.drop_constraint(op.f("provider_attempts_user_id_fkey"), "provider_attempts", type_="foreignkey")
    op.drop_constraint("fk_prompt_submissions_conversation_id_owner", "prompt_submissions", type_="foreignkey")
    op.drop_constraint("fk_prompt_submissions_replace_reply_message_id_owner", "prompt_submissions", type_="foreignkey")
    op.drop_constraint("fk_prompt_submissions_result_message_id_owner", "prompt_submissions", type_="foreignkey")
    op.drop_constraint(op.f("prompt_submissions_user_id_fkey"), "prompt_submissions", type_="foreignkey")
    op.drop_constraint("fk_post_publications_id_owner", "post_publications", type_="foreignkey")
    op.drop_constraint("fk_post_publications_post_id_owner", "post_publications", type_="foreignkey")
    op.drop_constraint("fk_post_publications_video_job_id_owner", "post_publications", type_="foreignkey")
    op.drop_constraint(op.f("personas_user_id_fkey"), "personas", type_="foreignkey")
    op.drop_constraint(op.f("nightly_activity_logs_user_id_fkey"), "nightly_activity_logs", type_="foreignkey")
    op.drop_constraint("fk_nightly_activity_actions_job_id_owner", "nightly_activity_actions", type_="foreignkey")
    op.drop_constraint("fk_nightly_activity_actions_log_id_owner", "nightly_activity_actions", type_="foreignkey")
    op.drop_constraint(op.f("nightly_activity_actions_user_id_fkey"), "nightly_activity_actions", type_="foreignkey")
    op.drop_constraint("fk_messages_owned_conversation", "messages", type_="foreignkey")
    op.drop_constraint(op.f("messages_user_id_fkey"), "messages", type_="foreignkey")
    op.drop_constraint("fk_message_voice_refs_message_id_owner", "message_voice_refs", type_="foreignkey")
    op.drop_constraint("fk_message_voice_refs_voice_id_owner", "message_voice_refs", type_="foreignkey")
    op.drop_constraint(op.f("message_voice_refs_user_id_fkey"), "message_voice_refs", type_="foreignkey")
    op.drop_constraint("fk_message_assets_asset_id_owner", "message_assets", type_="foreignkey")
    op.drop_constraint("fk_message_assets_message_id_owner", "message_assets", type_="foreignkey")
    op.drop_constraint(op.f("message_assets_user_id_fkey"), "message_assets", type_="foreignkey")
    op.drop_constraint("fk_memory_revision_owner", "memory_revisions", type_="foreignkey")
    op.drop_constraint(op.f("memory_revisions_user_id_fkey"), "memory_revisions", type_="foreignkey")
    op.drop_constraint("fk_memory_fingerprint_owner", "memory_fingerprints", type_="foreignkey")
    op.drop_constraint(op.f("memory_fingerprints_user_id_fkey"), "memory_fingerprints", type_="foreignkey")
    op.drop_constraint("fk_memory_evidence_message_owner", "memory_evidence", type_="foreignkey")
    op.drop_constraint("fk_memory_evidence_owner", "memory_evidence", type_="foreignkey")
    op.drop_constraint(op.f("memory_evidence_user_id_fkey"), "memory_evidence", type_="foreignkey")
    op.drop_constraint("fk_memory_embedding_owner", "memory_embeddings", type_="foreignkey")
    op.drop_constraint(op.f("memory_embeddings_user_id_fkey"), "memory_embeddings", type_="foreignkey")
    op.drop_constraint("fk_memories_source_conversation", "memories", type_="foreignkey")
    op.drop_constraint("fk_memories_source_event", "memories", type_="foreignkey")
    op.drop_constraint(op.f("memories_user_id_fkey"), "memories", type_="foreignkey")
    op.drop_constraint(op.f("login_records_user_id_fkey"), "login_records", type_="foreignkey")
    op.drop_constraint("fk_life_step_result_version", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_steps_job_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_steps_pursuit_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_steps_result_entry_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_steps_result_job_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_steps_result_visual_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_steps_result_visual_version_id_owner", "life_steps", type_="foreignkey")
    op.drop_constraint(op.f("life_steps_user_id_fkey"), "life_steps", type_="foreignkey")
    op.drop_constraint("fk_life_states_active_pursuit_id_owner", "life_states", type_="foreignkey")
    op.drop_constraint(op.f("life_states_user_id_fkey"), "life_states", type_="foreignkey")
    op.drop_constraint(op.f("life_pursuits_user_id_fkey"), "life_pursuits", type_="foreignkey")
    op.drop_constraint("fk_life_event_result_version", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_events_pursuit_id_owner", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_events_result_entry_id_owner", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_events_result_job_id_owner", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_events_result_visual_id_owner", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_events_result_visual_version_id_owner", "life_events", type_="foreignkey")
    op.drop_constraint("fk_life_events_step_id_owner", "life_events", type_="foreignkey")
    op.drop_constraint(op.f("life_events_user_id_fkey"), "life_events", type_="foreignkey")
    op.drop_constraint(op.f("jobs_user_id_fkey"), "jobs", type_="foreignkey")
    op.drop_constraint("fk_job_requests_owned_job", "job_requests", type_="foreignkey")
    op.drop_constraint(op.f("job_requests_user_id_fkey"), "job_requests", type_="foreignkey")
    op.drop_constraint("fk_job_assets_owned_asset", "job_assets", type_="foreignkey")
    op.drop_constraint("fk_job_assets_owned_job", "job_assets", type_="foreignkey")
    op.drop_constraint(op.f("job_assets_user_id_fkey"), "job_assets", type_="foreignkey")
    op.drop_constraint(op.f("entry_links_user_id_fkey"), "entry_links", type_="foreignkey")
    op.drop_constraint("fk_entry_links_source", "entry_links", type_="foreignkey")
    op.drop_constraint("fk_entry_links_target", "entry_links", type_="foreignkey")
    op.drop_constraint(op.f("entry_comments_user_id_fkey"), "entry_comments", type_="foreignkey")
    op.drop_constraint("fk_entry_comments_entry", "entry_comments", type_="foreignkey")
    op.drop_constraint("fk_entry_comments_reply", "entry_comments", type_="foreignkey")
    op.drop_constraint(op.f("entry_assets_user_id_fkey"), "entry_assets", type_="foreignkey")
    op.drop_constraint("fk_entry_assets_asset", "entry_assets", type_="foreignkey")
    op.drop_constraint("fk_entry_assets_entry", "entry_assets", type_="foreignkey")
    op.drop_constraint(op.f("custom_voices_user_id_fkey"), "custom_voices", type_="foreignkey")
    op.drop_constraint("fk_custom_voices_preview_asset_id_owner", "custom_voices", type_="foreignkey")
    op.drop_constraint("fk_custom_voices_reference_asset_id_owner", "custom_voices", type_="foreignkey")
    op.drop_constraint(op.f("cron_jobs_user_id_fkey"), "cron_jobs", type_="foreignkey")
    op.drop_constraint("fk_cron_jobs_conversation_id_owner", "cron_jobs", type_="foreignkey")
    op.drop_constraint(op.f("conversations_user_id_fkey"), "conversations", type_="foreignkey")
    op.drop_constraint("fk_conversations_forked_from_id_owner", "conversations", type_="foreignkey")
    op.drop_constraint("fk_conversations_parent_id_owner", "conversations", type_="foreignkey")
    op.drop_constraint(op.f("companion_topics_user_id_fkey"), "companion_topics", type_="foreignkey")
    op.drop_constraint("fk_companion_topics_batch_id_owner", "companion_topics", type_="foreignkey")
    op.drop_constraint(op.f("companion_topic_usages_user_id_fkey"), "companion_topic_usages", type_="foreignkey")
    op.drop_constraint("fk_companion_topic_usages_message_id_owner", "companion_topic_usages", type_="foreignkey")
    op.drop_constraint("fk_companion_topic_usages_topic_id_owner", "companion_topic_usages", type_="foreignkey")
    op.drop_constraint(op.f("companion_topic_feedback_user_id_fkey"), "companion_topic_feedback", type_="foreignkey")
    op.drop_constraint("fk_companion_topic_feedback_topic_id_owner", "companion_topic_feedback", type_="foreignkey")
    op.drop_constraint(op.f("companion_topic_batches_user_id_fkey"), "companion_topic_batches", type_="foreignkey")
    op.drop_constraint(op.f("companion_scenes_user_id_fkey"), "companion_scenes", type_="foreignkey")
    op.drop_constraint("fk_companion_scenes_initial_job_id_owner", "companion_scenes", type_="foreignkey")
    op.drop_constraint("fk_companion_scenes_media_asset_id_owner", "companion_scenes", type_="foreignkey")
    op.drop_constraint("fk_companion_scenes_replacement_job_id_owner", "companion_scenes", type_="foreignkey")
    op.drop_constraint("fk_companion_scenes_upload_asset_id_owner", "companion_scenes", type_="foreignkey")
    op.drop_constraint(op.f("companion_outfits_user_id_fkey"), "companion_outfits", type_="foreignkey")
    op.drop_constraint("fk_companion_outfits_asset_id_owner", "companion_outfits", type_="foreignkey")
    op.drop_constraint(op.f("companion_intents_user_id_fkey"), "companion_intents", type_="foreignkey")
    op.drop_constraint("fk_companion_intents_cron_job_id_owner", "companion_intents", type_="foreignkey")
    op.drop_constraint(
        op.f("companion_fullbody_candidates_user_id_fkey"), "companion_fullbody_candidates", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_companion_fullbody_candidates_asset_id_owner", "companion_fullbody_candidates", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_companion_fullbody_candidates_avatar_id_owner", "companion_fullbody_candidates", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_companion_fullbody_candidates_base_asset_id_owner", "companion_fullbody_candidates", type_="foreignkey"
    )
    op.drop_constraint(op.f("companion_entries_user_id_fkey"), "companion_entries", type_="foreignkey")
    op.drop_constraint("fk_character_analysis_jobs_avatar_id_owner", "character_analysis_jobs", type_="foreignkey")
    op.drop_constraint("fk_character_analysis_jobs_id_owner", "character_analysis_jobs", type_="foreignkey")
    op.drop_constraint(op.f("avatar_assets_user_id_fkey"), "avatar_assets", type_="foreignkey")
    op.drop_constraint("fk_avatar_assets_analysis_job_id_owner", "avatar_assets", type_="foreignkey")
    op.drop_constraint("fk_avatar_assets_fullbody_asset_id_owner", "avatar_assets", type_="foreignkey")
    op.drop_constraint("fk_avatar_assets_portrait_asset_id_owner", "avatar_assets", type_="foreignkey")
    op.drop_constraint(op.f("assets_user_id_fkey"), "assets", type_="foreignkey")
    op.drop_constraint(op.f("appearance_assets_user_id_fkey"), "appearance_assets", type_="foreignkey")
    op.drop_constraint("fk_appearance_assets_analysis_job_id_owner", "appearance_assets", type_="foreignkey")
    op.drop_constraint("fk_appearance_assets_asset_id_owner", "appearance_assets", type_="foreignkey")
    op.drop_constraint("fk_appearance_assets_avatar_id_owner", "appearance_assets", type_="foreignkey")
    op.drop_constraint("fk_appearance_assets_candidate_id_owner", "appearance_assets", type_="foreignkey")
    op.drop_constraint("fk_appearance_assets_outfit_id_owner", "appearance_assets", type_="foreignkey")
    op.drop_constraint(op.f("action_definitions_user_id_fkey"), "action_definitions", type_="foreignkey")
    op.drop_table("life_action_admissions")
    op.drop_table("outfit_creation_jobs")
    op.drop_table("ws_events")
    op.drop_table("visual_versions")
    op.drop_table("visual_version_assets")
    op.drop_table("visual_presentations")
    op.drop_table("visual_playbacks")
    op.drop_table("visual_jobs")
    op.drop_table("visual_items")
    op.drop_table("video_gen_jobs")
    op.drop_table("users")
    op.drop_table("user_social_accounts")
    op.drop_table("user_settings")
    op.drop_table("user_model_configs")
    op.drop_table("update_versions")
    op.drop_table("system_settings")
    op.drop_table("submission_messages")
    op.drop_table("scene_jobs")
    op.drop_table("remote_sessions")
    op.drop_table("remote_pairings")
    op.drop_table("pursuit_evidence")
    op.drop_table("provider_lanes")
    op.drop_table("provider_attempts")
    op.drop_table("prompt_submissions")
    op.drop_table("post_publications")
    op.drop_table("personas")
    op.drop_table("nightly_activity_logs")
    op.drop_table("nightly_activity_actions")
    op.drop_table("messages")
    op.drop_table("message_voice_refs")
    op.drop_table("message_assets")
    op.drop_table("memory_revisions")
    op.drop_table("memory_fingerprints")
    op.drop_table("memory_evidence")
    op.drop_table("memory_embeddings")
    op.drop_table("memories")
    op.drop_table("login_records")
    op.drop_table("life_steps")
    op.drop_table("life_states")
    op.drop_table("life_pursuits")
    op.drop_table("life_events")
    op.drop_table("jobs")
    op.drop_table("job_requests")
    op.drop_table("job_assets")
    op.drop_table("entry_links")
    op.drop_table("entry_comments")
    op.drop_table("entry_assets")
    op.drop_table("custom_voices")
    op.drop_table("cron_jobs")
    op.drop_table("conversations")
    op.drop_table("companion_topics")
    op.drop_table("companion_topic_usages")
    op.drop_table("companion_topic_feedback")
    op.drop_table("companion_topic_batches")
    op.drop_table("companion_scenes")
    op.drop_table("companion_outfits")
    op.drop_table("companion_intents")
    op.drop_table("companion_fullbody_candidates")
    op.drop_table("companion_entries")
    op.drop_table("character_analysis_jobs")
    op.drop_table("avatar_assets")
    op.drop_table("assets")
    op.drop_table("appearance_assets")
    op.drop_table("admin_sessions")
    op.drop_table("action_definitions")
