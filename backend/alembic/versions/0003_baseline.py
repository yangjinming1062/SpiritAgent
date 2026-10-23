"""当前数据库结构基线。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "admin_sessions",
        sa.Column("token_jti", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("admin_sessions_pkey")),
        sa.UniqueConstraint("token_jti", name="uq_admin_sessions_token_jti"),
    )
    op.create_index(op.f("ix_admin_sessions_created_at"), "admin_sessions", ["created_at"], unique=False)
    op.create_index(op.f("ix_admin_sessions_is_active"), "admin_sessions", ["is_active"], unique=False)
    op.create_index(op.f("ix_admin_sessions_username"), "admin_sessions", ["username"], unique=False)
    op.create_table(
        "system_settings",
        sa.Column("setting_key", sa.String(length=128), nullable=False),
        sa.Column("setting_value", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("system_settings_pkey")),
    )
    op.create_index(op.f("ix_system_settings_setting_key"), "system_settings", ["setting_key"], unique=True)
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
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("update_versions_pkey")),
    )
    op.create_index(op.f("ix_update_versions_is_active"), "update_versions", ["is_active"], unique=False)
    op.create_index(op.f("ix_update_versions_version"), "update_versions", ["version"], unique=True)
    op.create_table(
        "users",
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("activation_code", sa.Text(), nullable=False),
        sa.Column("activation_token_hash", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("nightly_activity_enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("users_pkey")),
    )
    op.create_index(op.f("ix_users_activation_token_hash"), "users", ["activation_token_hash"], unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "video_queue_lanes",
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("video_queue_lanes_pkey")),
        sa.UniqueConstraint("provider", "credential_fingerprint", name="uq_video_queue_credential"),
        sa.UniqueConstraint("token", name=op.f("uq_video_queue_lanes_token")),
    )
    op.create_table(
        "action_definitions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("motion_description", sa.Text(), nullable=False),
        sa.Column("use_when_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("avoid_when_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("allowed_triggers_json", sa.Text(), server_default=sa.text('\'["chat", "manual"]\''), nullable=False),
        sa.Column("clip_kind", sa.String(length=8), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("review_reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("review_source_json", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("action_definitions_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("action_definitions_pkey")),
    )
    op.create_index(
        op.f("ix_action_definitions_normalized_name"),
        "action_definitions",
        ["normalized_name"],
        unique=False,
    )
    op.create_index(op.f("ix_action_definitions_status"), "action_definitions", ["status"], unique=False)
    op.create_index(op.f("ix_action_definitions_user_id"), "action_definitions", ["user_id"], unique=False)
    op.create_table(
        "asset_cleanup_pending",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("asset_cleanup_pending_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("asset_cleanup_pending_pkey")),
        sa.UniqueConstraint("user_id", "path", name="uq_asset_cleanup_pending_user_path"),
    )
    op.create_index(op.f("ix_asset_cleanup_pending_user_id"), "asset_cleanup_pending", ["user_id"], unique=False)
    op.create_table(
        "avatar_assets",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("prompt_json", sa.Text(), nullable=False),
        sa.Column("asset_url", sa.String(length=2048), nullable=False),
        sa.Column("generation_origin_json", sa.Text(), nullable=True),
        sa.Column("fullbody_generation_origin_json", sa.Text(), nullable=True),
        sa.Column("seed_fullbody_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("is_fullbody_confirmed", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("avatar_assets_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("avatar_assets_pkey")),
    )
    op.create_index(op.f("ix_avatar_assets_active"), "avatar_assets", ["active"], unique=False)
    op.create_index(op.f("ix_avatar_assets_user_id"), "avatar_assets", ["user_id"], unique=False)
    op.create_index(
        "uq_avatar_assets_one_active",
        "avatar_assets",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "companion_diary_entries",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("post_ids", sa.ARRAY(sa.String()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_diary_entries_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_diary_entries_pkey")),
        sa.UniqueConstraint("user_id", "entry_date", name="uq_companion_diary_user_date"),
    )
    op.create_index(
        op.f("ix_companion_diary_entries_entry_date"),
        "companion_diary_entries",
        ["entry_date"],
        unique=False,
    )
    op.create_index(op.f("ix_companion_diary_entries_user_id"), "companion_diary_entries", ["user_id"], unique=False)
    op.create_index(
        "ix_companion_diary_unread_user",
        "companion_diary_entries",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("is_read IS FALSE"),
    )
    op.create_table(
        "companion_intents",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("source_key", sa.String(length=128), nullable=True),
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
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_intents_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_intents_pkey")),
    )
    op.create_index(op.f("ix_companion_intents_expires_at"), "companion_intents", ["expires_at"], unique=False)
    op.create_index(op.f("ix_companion_intents_status"), "companion_intents", ["status"], unique=False)
    op.create_index(op.f("ix_companion_intents_user_id"), "companion_intents", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_intents_wake_at"), "companion_intents", ["wake_at"], unique=False)
    op.create_table(
        "companion_outfits",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("description_status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("description_error", sa.Text(), nullable=True),
        sa.Column("fullbody_url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("source_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("is_initial", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_outfits_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_outfits_pkey")),
    )
    op.create_index(op.f("ix_companion_outfits_status"), "companion_outfits", ["status"], unique=False)
    op.create_index(op.f("ix_companion_outfits_user_id"), "companion_outfits", ["user_id"], unique=False)
    op.create_table(
        "companion_posts",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("content_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("media_url", sa.String(length=2048), nullable=True),
        sa.Column("audio_url", sa.String(length=2048), nullable=True),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_posts_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_posts_pkey")),
    )
    op.create_index(op.f("ix_companion_posts_activity_date"), "companion_posts", ["activity_date"], unique=False)
    op.create_index(op.f("ix_companion_posts_published_at"), "companion_posts", ["published_at"], unique=False)
    op.create_index(
        "ix_companion_posts_unread_user",
        "companion_posts",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("is_read IS FALSE"),
    )
    op.create_index(op.f("ix_companion_posts_user_id"), "companion_posts", ["user_id"], unique=False)
    op.create_table(
        "companion_scenes",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("stage", sa.String(length=24), server_default=sa.text("'prepare'"), nullable=False),
        sa.Column("origin", sa.String(length=16), server_default=sa.text("'user_request'"), nullable=False),
        sa.Column("source", sa.String(length=16), server_default=sa.text("'generated'"), nullable=False),
        sa.Column("target_size_json", sa.Text(), nullable=True),
        sa.Column("source_size_json", sa.Text(), nullable=True),
        sa.Column("image_size_json", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=80), server_default=sa.text("''"), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("requirements", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("prompt", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("media_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("generation_origin_json", sa.Text(), nullable=True),
        sa.Column("upload_source_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("generation_state_json", sa.Text(), nullable=True),
        sa.Column("regeneration_status", sa.String(length=24), nullable=True),
        sa.Column("regeneration_stage", sa.String(length=24), nullable=True),
        sa.Column("regeneration_error", sa.Text(), nullable=True),
        sa.Column("regeneration_task_id", sa.String(length=36), nullable=True),
        sa.Column("regeneration_state_json", sa.Text(), nullable=True),
        sa.Column("reference_image", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_scenes_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_scenes_pkey")),
    )
    op.create_index(
        op.f("ix_companion_scenes_regeneration_status"),
        "companion_scenes",
        ["regeneration_status"],
        unique=False,
    )
    op.create_index(op.f("ix_companion_scenes_status"), "companion_scenes", ["status"], unique=False)
    op.create_index(op.f("ix_companion_scenes_user_id"), "companion_scenes", ["user_id"], unique=False)
    op.create_table(
        "companion_topic_batches",
        sa.Column("user_id", sa.Integer(), nullable=False),
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
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(length=160), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_topic_batches_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_topic_batches_pkey")),
        sa.UniqueConstraint("user_id", "system_preset_id", "activity_date", name="uq_topic_batch_scope_date"),
    )
    op.create_index(op.f("ix_companion_topic_batches_user_id"), "companion_topic_batches", ["user_id"], unique=False)
    op.create_table(
        "conversations",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("forked_from_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), server_default=sa.text("'standard'"), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("memory_reviewed_message_id", sa.Integer(), server_default="0", nullable=False),
        sa.Column("context_after_message_id", sa.Integer(), server_default="0", nullable=False),
        sa.Column("history_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settings_json", sa.Text(), nullable=True),
        sa.Column("is_automation", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("is_deletable", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("is_renamable", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
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
            "context_after_message_id >= 0",
            name=op.f("ck_conversations_ck_conversations_context_watermark"),
        ),
        sa.ForeignKeyConstraint(
            ["forked_from_id"],
            ["conversations.id"],
            name=op.f("conversations_forked_from_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["conversations.id"],
            name=op.f("conversations_parent_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("conversations_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("conversations_pkey")),
    )
    op.create_index(op.f("ix_conversations_forked_from_id"), "conversations", ["forked_from_id"], unique=False)
    op.create_index(op.f("ix_conversations_parent_id"), "conversations", ["parent_id"], unique=False)
    op.create_index(op.f("ix_conversations_system_preset_id"), "conversations", ["system_preset_id"], unique=False)
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)
    op.create_index(
        "uq_conversations_user_preset",
        "conversations",
        ["user_id", "system_preset_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'special' AND system_preset_id IS NOT NULL"),
    )
    op.create_table(
        "custom_voices",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("model_card_id", sa.String(length=36), nullable=False),
        sa.Column("provider_voice_id", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("reference_path", sa.Text(), nullable=True),
        sa.Column("preview_path", sa.Text(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expected_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expected_voice_id", sa.Text(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('sample', 'design', 'clone')", name=op.f("ck_custom_voices_voice_kind")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("custom_voices_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("custom_voices_pkey")),
    )
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
    op.create_table(
        "login_records",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_jti", sa.String(length=64), nullable=False),
        sa.Column("client_version", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("login_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("logout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("login_records_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("login_records_pkey")),
        sa.UniqueConstraint("token_jti", name="uq_login_records_token_jti"),
    )
    op.create_index(op.f("ix_login_records_is_active"), "login_records", ["is_active"], unique=False)
    op.create_index(op.f("ix_login_records_login_at"), "login_records", ["login_at"], unique=False)
    op.create_index(op.f("ix_login_records_user_id"), "login_records", ["user_id"], unique=False)
    op.create_table(
        "memories",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("basis", sa.String(length=16), server_default="system", nullable=False),
        sa.Column("usage", sa.String(length=16), server_default="contextual", nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("importance", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("embedding", Vector(dim=1536), nullable=True),
        sa.Column("embedding_model_key", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "basis IN ('explicit', 'inferred', 'observed', 'system')",
            name=op.f("ck_memories_ck_memories_basis"),
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'active', 'invalidated', 'forgotten')",
            name=op.f("ck_memories_ck_memories_status"),
        ),
        sa.CheckConstraint("usage IN ('contextual', 'background')", name=op.f("ck_memories_ck_memories_usage")),
        sa.CheckConstraint("content_version > 0", name=op.f("ck_memories_ck_memories_content_version")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("memories_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("memories_pkey")),
    )
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
        "ix_memories_embedding",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_memories_recall_user_updated",
        "memories",
        ["user_id", sa.literal_column("updated_at DESC")],
        unique=False,
        postgresql_where=sa.text("context LIKE 'recall:%'"),
    )
    op.create_index(
        "ix_memories_scope_updated",
        "memories",
        ["user_id", "system_preset_id", sa.literal_column("updated_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(op.f("ix_memories_user_id"), "memories", ["user_id"], unique=False)
    op.create_index(
        "uq_memories_diary_day",
        "memories",
        ["user_id", "system_preset_id", "context"],
        unique=True,
        postgresql_where=sa.text("context LIKE 'diary:%'"),
    )
    op.create_index(
        "uq_memories_interaction_day",
        "memories",
        ["user_id", "system_preset_id", "context"],
        unique=True,
        postgresql_where=sa.text("context LIKE 'interaction_stats:%'"),
    )
    op.create_index(
        "uq_memories_nightly_actions",
        "memories",
        ["user_id", "system_preset_id", "context"],
        unique=True,
        postgresql_where=sa.text("context LIKE 'recall:nightly_actions:%'"),
    )
    op.create_index(
        "uq_memories_reflection_slot",
        "memories",
        ["user_id", "system_preset_id", "context"],
        unique=True,
        postgresql_where=sa.text("context LIKE 'reflection:%'"),
    )
    op.create_index(
        "uq_memories_user_context",
        "memories",
        ["user_id", "system_preset_id", "context"],
        unique=True,
        postgresql_where=sa.text("context LIKE 'user_profile:%'"),
    )
    op.create_table(
        "nightly_activity_logs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'running'"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("nightly_activity_logs_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("nightly_activity_logs_pkey")),
        sa.UniqueConstraint("user_id", "target_date", name="uq_nightly_activity_logs_user_date"),
    )
    op.create_index(
        op.f("ix_nightly_activity_logs_target_date"),
        "nightly_activity_logs",
        ["target_date"],
        unique=False,
    )
    op.create_index(op.f("ix_nightly_activity_logs_user_id"), "nightly_activity_logs", ["user_id"], unique=False)
    op.create_table(
        "personas",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("personality_tags_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("is_complete", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("is_portrait_confirmed", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("portrait_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scene_policy", sa.String(length=16), server_default=sa.text("'llm_may_replace'"), nullable=False),
        sa.Column("outfit_policy", sa.String(length=16), server_default=sa.text("'llm_may_replace'"), nullable=False),
        sa.Column("current_mood", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("personas_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("personas_pkey")),
    )
    op.create_index(op.f("ix_personas_is_complete"), "personas", ["is_complete"], unique=False)
    op.create_index(op.f("ix_personas_is_portrait_confirmed"), "personas", ["is_portrait_confirmed"], unique=False)
    op.create_index(op.f("ix_personas_user_id"), "personas", ["user_id"], unique=True)
    op.create_table(
        "remote_pairings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("remote_pairings_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("remote_pairings_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_remote_pairings_token_hash")),
    )
    op.create_index(op.f("ix_remote_pairings_user_id"), "remote_pairings", ["user_id"], unique=False)
    op.create_table(
        "remote_sessions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("remote_sessions_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("remote_sessions_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_remote_sessions_token_hash")),
    )
    op.create_index(op.f("ix_remote_sessions_expires_at"), "remote_sessions", ["expires_at"], unique=False)
    op.create_index(op.f("ix_remote_sessions_user_id"), "remote_sessions", ["user_id"], unique=False)
    op.create_table(
        "user_model_configs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ai_config", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("user_model_configs_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("user_model_configs_pkey")),
    )
    op.create_index(op.f("ix_user_model_configs_user_id"), "user_model_configs", ["user_id"], unique=True)
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("setting_key", sa.String(length=128), nullable=False),
        sa.Column("setting_value", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("user_settings_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("user_settings_pkey")),
        sa.UniqueConstraint("user_id", "setting_key", name="uq_user_settings_user_key"),
    )
    op.create_index(op.f("ix_user_settings_setting_key"), "user_settings", ["setting_key"], unique=False)
    op.create_index(op.f("ix_user_settings_user_id"), "user_settings", ["user_id"], unique=False)
    op.create_table(
        "video_queue_requests",
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("lane_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("frozen_json", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("error_status_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("rate_limit_count", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lane_id"], ["video_queue_lanes.id"], name=op.f("video_queue_requests_lane_id_fkey")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("video_queue_requests_user_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("video_queue_requests_pkey")),
        sa.UniqueConstraint("generation_id", "attempt_index", name="uq_video_queue_attempt"),
        sa.UniqueConstraint("token", name=op.f("uq_video_queue_requests_token")),
    )
    op.create_index(
        "ix_video_queue_fifo",
        "video_queue_requests",
        ["lane_id", "state", "enqueued_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_video_queue_owner",
        "video_queue_requests",
        ["kind", "entity_id", "generation_id"],
        unique=False,
    )
    op.create_index(op.f("ix_video_queue_requests_state"), "video_queue_requests", ["state"], unique=False)
    op.create_index(op.f("ix_video_queue_requests_user_id"), "video_queue_requests", ["user_id"], unique=False)
    op.create_table(
        "ws_events",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("ws_events_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("ws_events_pkey")),
    )
    op.create_index(op.f("ix_ws_events_created_at"), "ws_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_ws_events_next_retry_at"), "ws_events", ["next_retry_at"], unique=False)
    op.create_index("ix_ws_events_poll", "ws_events", ["user_id", "status", "next_retry_at"], unique=False)
    op.create_index(op.f("ix_ws_events_status"), "ws_events", ["status"], unique=False)
    op.create_index(op.f("ix_ws_events_user_id"), "ws_events", ["user_id"], unique=False)
    op.create_table(
        "companion_character_cards",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("automatic_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("overrides_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("extraction_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("portrait_status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("body_status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("portrait_result_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("body_result_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("portrait_source_path", sa.String(length=2048), nullable=False),
        sa.Column("body_source_path", sa.String(length=2048), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["avatar_id"],
            ["avatar_assets.id"],
            name=op.f("companion_character_cards_avatar_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_character_cards_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_character_cards_pkey")),
        sa.UniqueConstraint("avatar_id", name=op.f("uq_companion_character_cards_avatar_id")),
    )
    op.create_index(op.f("ix_companion_character_cards_status"), "companion_character_cards", ["status"], unique=False)
    op.create_index(
        op.f("ix_companion_character_cards_user_id"),
        "companion_character_cards",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "companion_fullbody_candidates",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=False),
        sa.Column("base_fullbody_url", sa.String(length=2048), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.String(length=2048), nullable=False),
        sa.Column("generation_origin_json", sa.Text(), nullable=True),
        sa.Column("body_features_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["avatar_id"],
            ["avatar_assets.id"],
            name=op.f("companion_fullbody_candidates_avatar_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_fullbody_candidates_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_fullbody_candidates_pkey")),
    )
    op.create_index(
        op.f("ix_companion_fullbody_candidates_avatar_id"),
        "companion_fullbody_candidates",
        ["avatar_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_companion_fullbody_candidates_status"),
        "companion_fullbody_candidates",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_companion_fullbody_candidates_user_id"),
        "companion_fullbody_candidates",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "companion_post_comments",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("post_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reply_to_comment_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("reply_status", sa.String(length=16), server_default=sa.text("'none'"), nullable=False),
        sa.Column("reply_error", sa.String(length=160), nullable=True),
        sa.Column("reply_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["companion_posts.id"],
            name=op.f("companion_post_comments_post_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reply_to_comment_id"],
            ["companion_post_comments.id"],
            name=op.f("companion_post_comments_reply_to_comment_id_fkey"),
            ondelete="SET NULL",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_post_comments_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_post_comments_pkey")),
        sa.UniqueConstraint("reply_to_comment_id", name="uq_post_comment_reply_target"),
    )
    op.create_index(op.f("ix_companion_post_comments_post_id"), "companion_post_comments", ["post_id"], unique=False)
    op.create_index(
        op.f("ix_companion_post_comments_reply_status"),
        "companion_post_comments",
        ["reply_status"],
        unique=False,
    )
    op.create_index(op.f("ix_companion_post_comments_user_id"), "companion_post_comments", ["user_id"], unique=False)
    op.create_table(
        "companion_topics",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
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
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["companion_topic_batches.id"],
            name=op.f("companion_topics_batch_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_topics_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_topics_pkey")),
        sa.UniqueConstraint("user_id", "system_preset_id", "platform", "source_id", name="uq_topic_scope_source"),
    )
    op.create_index("ix_companion_topics_batch_id", "companion_topics", ["batch_id"], unique=False)
    op.create_index(op.f("ix_companion_topics_user_id"), "companion_topics", ["user_id"], unique=False)
    op.create_table(
        "cron_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("schedule", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default=sa.text("'standard'"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("is_paused", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("one_shot", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("cron_jobs_conversation_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("cron_jobs_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("cron_jobs_pkey")),
    )
    op.create_index(op.f("ix_cron_jobs_conversation_id"), "cron_jobs", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_cron_jobs_expires_at"), "cron_jobs", ["expires_at"], unique=False)
    op.create_index(op.f("ix_cron_jobs_name"), "cron_jobs", ["name"], unique=False)
    op.create_index(op.f("ix_cron_jobs_next_run_at"), "cron_jobs", ["next_run_at"], unique=False)
    op.create_index(op.f("ix_cron_jobs_user_id"), "cron_jobs", ["user_id"], unique=False)
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("subtype", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.Text(), nullable=True),
        sa.Column("tool_call_id", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("turn_duration_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("content_type", sa.String(length=32), server_default=sa.text("'text'"), nullable=False),
        sa.Column("media_json", sa.Text(), nullable=True),
        sa.Column("reasoning_content", sa.Text(), nullable=True),
        sa.Column("generation_origin_json", sa.Text(), nullable=True),
        sa.Column("reply_json", sa.Text(), nullable=True),
        sa.Column("summary_through_message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
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
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("messages_conversation_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("messages_pkey")),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_messages_subtype"), "messages", ["subtype"], unique=False)
    op.create_table(
        "nightly_activity_actions",
        sa.Column("log_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("action_key", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("arguments", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["log_id"],
            ["nightly_activity_logs.id"],
            name=op.f("nightly_activity_actions_log_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("nightly_activity_actions_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("nightly_activity_actions_pkey")),
        sa.UniqueConstraint("log_id", "action_key", name="uq_nightly_activity_actions_log_key"),
    )
    op.create_index(
        op.f("ix_nightly_activity_actions_capability"),
        "nightly_activity_actions",
        ["capability"],
        unique=False,
    )
    op.create_index(op.f("ix_nightly_activity_actions_log_id"), "nightly_activity_actions", ["log_id"], unique=False)
    op.create_index(op.f("ix_nightly_activity_actions_status"), "nightly_activity_actions", ["status"], unique=False)
    op.create_index(
        op.f("ix_nightly_activity_actions_target_date"),
        "nightly_activity_actions",
        ["target_date"],
        unique=False,
    )
    op.create_index(op.f("ix_nightly_activity_actions_user_id"), "nightly_activity_actions", ["user_id"], unique=False)
    op.create_table(
        "prompt_submissions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'accepted'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("origin_kind", sa.String(length=16), nullable=False),
        sa.Column("origin_id", sa.String(length=64), nullable=True),
        sa.Column("message_ids_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("replace_reply_message_id", sa.Integer(), nullable=True),
        sa.Column("result_message_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("prompt_submissions_conversation_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("prompt_submissions_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("prompt_submissions_pkey")),
        sa.UniqueConstraint("user_id", "request_id", name="uq_prompt_submissions_user_request"),
    )
    op.create_index(
        op.f("ix_prompt_submissions_conversation_id"),
        "prompt_submissions",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(op.f("ix_prompt_submissions_user_id"), "prompt_submissions", ["user_id"], unique=False)
    op.create_table(
        "scene_generation_attempts",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["companion_scenes.id"],
            name=op.f("scene_generation_attempts_scene_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("scene_generation_attempts_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("scene_generation_attempts_pkey")),
    )
    op.create_index(
        op.f("ix_scene_generation_attempts_user_id"),
        "scene_generation_attempts",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "visual_items",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("media_kind", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=160), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'ready'"), nullable=False),
        sa.Column("media_path", sa.Text(), nullable=False),
        sa.Column("poster_path", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("outfit_description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("outfit_id", sa.Integer(), nullable=True),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("input_image_id", sa.Integer(), nullable=True),
        sa.Column("input_image_path", sa.Text(), nullable=True),
        sa.Column("input_image_hash", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["action_definitions.id"],
            name=op.f("visual_items_action_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["input_image_id"],
            ["visual_items.id"],
            name=op.f("visual_items_input_image_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["outfit_id"],
            ["companion_outfits.id"],
            name=op.f("visual_items_outfit_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["companion_scenes.id"],
            name=op.f("visual_items_scene_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("visual_items_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("visual_items_pkey")),
    )
    op.create_index(op.f("ix_visual_items_action_id"), "visual_items", ["action_id"], unique=False)
    op.create_index(op.f("ix_visual_items_media_kind"), "visual_items", ["media_kind"], unique=False)
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
    op.create_table(
        "companion_topic_feedback",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=8), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["companion_topics.id"],
            name=op.f("companion_topic_feedback_topic_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_topic_feedback_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_topic_feedback_pkey")),
        sa.UniqueConstraint("topic_id", name="uq_topic_feedback_topic"),
    )
    op.create_index(
        op.f("ix_companion_topic_feedback_topic_id"),
        "companion_topic_feedback",
        ["topic_id"],
        unique=False,
    )
    op.create_index(op.f("ix_companion_topic_feedback_user_id"), "companion_topic_feedback", ["user_id"], unique=False)
    op.create_table(
        "companion_topic_usages",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("companion_topic_usages_message_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["companion_topics.id"],
            name=op.f("companion_topic_usages_topic_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("companion_topic_usages_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("companion_topic_usages_pkey")),
        sa.UniqueConstraint("topic_id", "message_id", name="uq_topic_usage_message"),
    )
    op.create_index(
        op.f("ix_companion_topic_usages_message_id"),
        "companion_topic_usages",
        ["message_id"],
        unique=False,
    )
    op.create_index(op.f("ix_companion_topic_usages_topic_id"), "companion_topic_usages", ["topic_id"], unique=False)
    op.create_index(op.f("ix_companion_topic_usages_user_id"), "companion_topic_usages", ["user_id"], unique=False)
    op.create_table(
        "video_gen_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("structured_reply", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("media_id", sa.String(length=128), nullable=True),
        sa.Column("reply_message_id", sa.Integer(), nullable=True),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("cleanup_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("candidate_video_url", sa.Text(), nullable=True),
        sa.Column("generation_state_json", sa.Text(), nullable=False),
        sa.Column("generation_attempt_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["reply_message_id"],
            ["messages.id"],
            name=op.f("video_gen_jobs_reply_message_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.id"],
            name=op.f("video_gen_jobs_source_message_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("video_gen_jobs_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("video_gen_jobs_pkey")),
        sa.UniqueConstraint("media_id", name=op.f("uq_video_gen_jobs_media_id")),
    )
    op.create_index(
        op.f("ix_video_gen_jobs_cleanup_requested_at"),
        "video_gen_jobs",
        ["cleanup_requested_at"],
        unique=False,
    )
    op.create_index(op.f("ix_video_gen_jobs_created_at"), "video_gen_jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_provider_task_id"), "video_gen_jobs", ["provider_task_id"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_reply_message_id"), "video_gen_jobs", ["reply_message_id"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_source_message_id"), "video_gen_jobs", ["source_message_id"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_status"), "video_gen_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_user_id"), "video_gen_jobs", ["user_id"], unique=False)
    op.create_index("ix_video_gen_jobs_user_status", "video_gen_jobs", ["user_id", "status"], unique=False)
    op.create_table(
        "visual_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_visual_id", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.String(length=16), server_default=sa.text("'create'"), nullable=False),
        sa.Column("target_kind", sa.String(length=8), server_default=sa.text("'video'"), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("feedback", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("video_feedback", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("state", sa.String(length=24), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("stage", sa.String(length=24), server_default=sa.text("'prepare'"), nullable=False),
        sa.Column("progress_json", sa.Text(), nullable=True),
        sa.Column("candidates_json", sa.Text(), nullable=True),
        sa.Column("repair_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("repair_limit", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_visual_id", sa.Integer(), nullable=True),
        sa.Column("result_image_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["result_image_id"],
            ["visual_items.id"],
            name=op.f("visual_jobs_result_image_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["result_visual_id"],
            ["visual_items.id"],
            name=op.f("visual_jobs_result_visual_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_visual_id"],
            ["visual_items.id"],
            name=op.f("visual_jobs_target_visual_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("visual_jobs_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("visual_jobs_pkey")),
    )
    op.create_index(op.f("ix_visual_jobs_state"), "visual_jobs", ["state"], unique=False)
    op.create_index(op.f("ix_visual_jobs_target_visual_id"), "visual_jobs", ["target_visual_id"], unique=False)
    op.create_index(op.f("ix_visual_jobs_user_id"), "visual_jobs", ["user_id"], unique=False)
    op.create_table(
        "visual_playbacks",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("visual_id", sa.Integer(), nullable=False),
        sa.Column("visual_version", sa.Integer(), nullable=False),
        sa.Column("play_id", sa.String(length=36), nullable=False),
        sa.Column("presentation_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("state_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_kind", sa.String(length=24), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("visual_playbacks_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["visual_id"],
            ["visual_items.id"],
            name=op.f("visual_playbacks_visual_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("visual_playbacks_pkey")),
        sa.UniqueConstraint("play_id", name=op.f("uq_visual_playbacks_play_id")),
    )
    op.create_index(op.f("ix_visual_playbacks_user_id"), "visual_playbacks", ["user_id"], unique=False)
    op.create_index(op.f("ix_visual_playbacks_visual_id"), "visual_playbacks", ["visual_id"], unique=False)
    op.create_table(
        "visual_presentations",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("current_visual_id", sa.Integer(), nullable=True),
        sa.Column("loop_visual_id", sa.Integer(), nullable=True),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("autonomous_enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("target_width", sa.Integer(), nullable=True),
        sa.Column("target_height", sa.Integer(), nullable=True),
        sa.Column("presentation_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("state_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pending_command_json", sa.Text(), nullable=True),
        sa.Column("current_snapshot_json", sa.Text(), nullable=True),
        sa.Column("loop_snapshot_json", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["current_visual_id"],
            ["visual_items.id"],
            name=op.f("visual_presentations_current_visual_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["loop_visual_id"],
            ["visual_items.id"],
            name=op.f("visual_presentations_loop_visual_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("visual_presentations_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("visual_presentations_pkey")),
    )
    op.create_index(op.f("ix_visual_presentations_user_id"), "visual_presentations", ["user_id"], unique=True)
    op.create_table(
        "post_publications",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("progress_json", sa.JSON(), nullable=False),
        sa.Column("video_job_id", sa.Integer(), nullable=True),
        sa.Column("post_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("error", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["companion_posts.id"],
            name=op.f("post_publications_post_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("post_publications_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_job_id"],
            ["video_gen_jobs.id"],
            name=op.f("post_publications_video_job_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("post_publications_pkey")),
    )
    op.create_index(op.f("ix_post_publications_reserved_at"), "post_publications", ["reserved_at"], unique=False)
    op.create_index(op.f("ix_post_publications_status"), "post_publications", ["status"], unique=False)
    op.create_index(op.f("ix_post_publications_user_id"), "post_publications", ["user_id"], unique=False)
    op.create_index(op.f("ix_post_publications_video_job_id"), "post_publications", ["video_job_id"], unique=False)

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
    op.execute("DROP TRIGGER IF EXISTS ws_event_notify_trigger ON ws_events")
    op.execute("DROP FUNCTION IF EXISTS notify_ws_event()")
    # 子表先于父表，保留数据库扩展。
    for table in (
        "post_publications",
        "visual_presentations",
        "visual_playbacks",
        "visual_jobs",
        "video_gen_jobs",
        "companion_topic_usages",
        "companion_topic_feedback",
        "visual_items",
        "scene_generation_attempts",
        "prompt_submissions",
        "nightly_activity_actions",
        "messages",
        "cron_jobs",
        "companion_topics",
        "companion_post_comments",
        "companion_fullbody_candidates",
        "companion_character_cards",
        "ws_events",
        "video_queue_requests",
        "user_settings",
        "user_model_configs",
        "remote_sessions",
        "remote_pairings",
        "personas",
        "nightly_activity_logs",
        "memories",
        "login_records",
        "custom_voices",
        "conversations",
        "companion_topic_batches",
        "companion_scenes",
        "companion_posts",
        "companion_outfits",
        "companion_intents",
        "companion_diary_entries",
        "avatar_assets",
        "asset_cleanup_pending",
        "action_definitions",
        "video_queue_lanes",
        "users",
        "update_versions",
        "system_settings",
        "admin_sessions",
    ):
        op.drop_table(table)
