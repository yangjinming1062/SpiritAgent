"""完整 schema 基线：pgvector/pg_trgm 扩展、partial unique 与 HNSW/GIN 索引、ws_events NOTIFY 触发器、2D 模型管线、persona.render_mode/outfit_policy/current_mood、IM 通道桥两表、messages.draft_anchor/reasoning_content/speech_style_json、房间背景/时刻/日记、nightly_activity_logs/actions、cron 双轨、user_model_configs.ai_config、system_settings。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# Alembic 用的版本标识符。
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 先建扩展：memories.embedding 是 vector(1536)，必须在 create_table 之前存在该类型。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "admin_sessions",
        sa.Column("token_jti", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("client_version", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_jti", name="uq_admin_sessions_token_jti"),
    )
    op.create_index(op.f("ix_admin_sessions_created_at"), "admin_sessions", ["created_at"], unique=False)
    op.create_index(op.f("ix_admin_sessions_is_active"), "admin_sessions", ["is_active"], unique=False)
    op.create_index(op.f("ix_admin_sessions_token_jti"), "admin_sessions", ["token_jti"], unique=False)
    op.create_index(op.f("ix_admin_sessions_username"), "admin_sessions", ["username"], unique=False)
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
        sa.Column("runner_sha512", sa.String(length=128), nullable=True),
        sa.Column("runner_size", sa.Integer(), nullable=True),
        sa.Column("runner_version", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_update_versions_is_active"), "update_versions", ["is_active"], unique=False)
    op.create_index(op.f("ix_update_versions_version"), "update_versions", ["version"], unique=True)
    op.create_table(
        "users",
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("activation_code", sa.Text(), nullable=True),
        sa.Column("activation_token_hash", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("nightly_activity_enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_activation_token_hash"), "users", ["activation_token_hash"], unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "avatar_assets",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("prompt_json", sa.Text(), nullable=False),
        sa.Column("asset_url", sa.String(length=2048), nullable=False),
        sa.Column("style", sa.String(length=64), nullable=False),
        sa.Column("seed_front_2d_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("seed_front_3d_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("seed_back_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_avatar_assets_active"), "avatar_assets", ["active"], unique=False)
    op.create_index(op.f("ix_avatar_assets_user_id"), "avatar_assets", ["user_id"], unique=False)
    op.create_table(
        "companion_3d_models",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("asset_url", sa.Text(), nullable=False),
        sa.Column("source_portrait_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("species", sa.String(length=64), server_default=sa.text("'人类'"), nullable=False),
        sa.Column("rig_type", sa.String(length=32), server_default=sa.text("'biped'"), nullable=False),
        sa.Column("rig_naming", sa.String(length=16), server_default=sa.text("'tripo'"), nullable=False),
        sa.Column("style", sa.String(length=16), server_default=sa.text("'realistic'"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("has_rig", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("clip_map_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("provider_phase", sa.String(length=16), server_default=sa.text("'submit'"), nullable=False),
        sa.Column("content_hash", sa.String(length=64), server_default=sa.text("''"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("download_urls_json", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_3d_models_active"), "companion_3d_models", ["active"], unique=False)
    op.create_index(op.f("ix_companion_3d_models_rig_type"), "companion_3d_models", ["rig_type"], unique=False)
    op.create_index(op.f("ix_companion_3d_models_user_id"), "companion_3d_models", ["user_id"], unique=False)
    op.create_table(
        "companion_outfits",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("fullbody_url", sa.String(length=2048), nullable=False),
        sa.Column("style", sa.String(length=32), server_default=sa.text("'cel_shading'"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("source_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("pending_wear", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_outfits_user_id"), "companion_outfits", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_outfits_status"), "companion_outfits", ["status"], unique=False)
    op.create_index(op.f("ix_companion_outfits_active"), "companion_outfits", ["active"], unique=False)
    op.create_table(
        "companion_2d_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=True),
        sa.Column("outfit_id", sa.Integer(), nullable=True),
        sa.Column("style", sa.String(length=32), server_default=sa.text("'cel_shading'"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'generating'"), nullable=False),
        sa.Column("manifest_json", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("manifest_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("layers_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=8), server_default=sa.text("'high'"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["outfit_id"], ["companion_outfits.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_2d_models_active"), "companion_2d_models", ["active"], unique=False)
    op.create_index(op.f("ix_companion_2d_models_status"), "companion_2d_models", ["status"], unique=False)
    op.create_index(op.f("ix_companion_2d_models_user_id"), "companion_2d_models", ["user_id"], unique=False)
    op.create_table(
        "conversations",
        sa.Column("memory_reviewed_message_id", sa.Integer(), server_default="0", nullable=False),
        sa.Column("context_after_message_id", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("context_after_message_id >= 0", name="ck_conversations_context_watermark"),
        sa.CheckConstraint(
            "is_automation = (system_preset_id = 'automation')",
            name="ck_conversations_automation_preset",
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), server_default=sa.text("'standard'"), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cwd", sa.String(length=1024), nullable=True),
        sa.Column("settings_json", sa.Text(), nullable=True),
        sa.Column("is_deletable", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("is_renamable", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("is_automation", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conversations_parent_id"), "conversations", ["parent_id"], unique=False)
    op.create_index(op.f("ix_conversations_system_preset_id"), "conversations", ["system_preset_id"], unique=False)
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)
    op.create_table(
        "cron_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("schedule", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("deliver", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default=sa.text("'standard'"), nullable=False),
        sa.Column("is_paused", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("one_shot", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cron_jobs_name"), "cron_jobs", ["name"], unique=False)
    op.create_index(op.f("ix_cron_jobs_next_run_at"), "cron_jobs", ["next_run_at"], unique=False)
    op.create_index(op.f("ix_cron_jobs_user_id"), "cron_jobs", ["user_id"], unique=False)
    op.create_index(op.f("ix_cron_jobs_conversation_id"), "cron_jobs", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_cron_jobs_expires_at"), "cron_jobs", ["expires_at"], unique=False)
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
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_jti", name="uq_login_records_token_jti"),
    )
    op.create_index(op.f("ix_login_records_is_active"), "login_records", ["is_active"], unique=False)
    op.create_index(op.f("ix_login_records_login_at"), "login_records", ["login_at"], unique=False)
    op.create_index(op.f("ix_login_records_token_jti"), "login_records", ["token_jti"], unique=False)
    op.create_index(op.f("ix_login_records_user_id"), "login_records", ["user_id"], unique=False)
    op.create_table(
        "memories",
        sa.Column("system_preset_id", sa.String(32), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_refs", JSONB(), nullable=False),
        sa.Column("content_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("content_version > 0", name="ck_memories_content_version"),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("basis", sa.String(16), server_default="system", nullable=False),
        sa.Column("usage", sa.String(16), server_default="contextual", nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("evidence", JSONB(), server_default="[]", nullable=False),
        sa.Column("history", JSONB(), server_default="[]", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('candidate', 'active', 'invalidated', 'forgotten')", name="ck_memories_status"),
        sa.CheckConstraint("basis IN ('explicit', 'inferred', 'observed', 'system')", name="ck_memories_basis"),
        sa.CheckConstraint("usage IN ('contextual', 'background')", name="ck_memories_usage"),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("importance", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("embedding", Vector(dim=1536), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_memories_user_id"), "memories", ["user_id"], unique=False)
    op.create_table(
        "companion_room_backdrops",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("origin", sa.String(length=16), server_default=sa.text("'onboarding'"), nullable=False),
        sa.Column("intent", sa.String(length=16), server_default=sa.text("'decorate'"), nullable=False),
        sa.Column("brief", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("prompt", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("media_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("public_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("seed_portrait_media_id", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("seed_outfit_media_id", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("outfit_fingerprint", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("contains_character", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("error_utterance", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_room_backdrops_user_id"), "companion_room_backdrops", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_room_backdrops_status"), "companion_room_backdrops", ["status"], unique=False)
    op.create_index(
        op.f("ix_companion_room_backdrops_outfit_fingerprint"),
        "companion_room_backdrops",
        ["outfit_fingerprint"],
        unique=False,
    )
    op.create_table(
        "personas",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("personality_tags_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("is_complete", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("is_portrait_confirmed", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("portrait_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("render_mode", sa.String(length=8), server_default=sa.text("'2d'"), nullable=False),
        sa.Column("active_backdrop_id", sa.Integer(), nullable=True),
        sa.Column("backdrop_policy", sa.String(length=16), server_default=sa.text("'llm_may_replace'"), nullable=False),
        sa.Column("outfit_policy", sa.String(length=16), server_default=sa.text("'llm_may_replace'"), nullable=False),
        sa.Column("current_mood", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["active_backdrop_id"], ["companion_room_backdrops.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_personas_is_complete"), "personas", ["is_complete"], unique=False)
    op.create_index(op.f("ix_personas_is_portrait_confirmed"), "personas", ["is_portrait_confirmed"], unique=False)
    op.create_index(op.f("ix_personas_render_mode"), "personas", ["render_mode"], unique=False)
    op.create_index(op.f("ix_personas_user_id"), "personas", ["user_id"], unique=True)
    op.create_table(
        "companion_moments",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default=sa.text("'greeting'"), nullable=False),
        sa.Column("title", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("emotion", sa.String(length=32), nullable=True),
        sa.Column("media_url", sa.String(length=2048), nullable=True),
        sa.Column("source", sa.String(length=16), server_default=sa.text("'system'"), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("visibility", sa.String(length=16), server_default=sa.text("'shown'"), nullable=False),
        sa.Column("media_type", sa.String(length=16), server_default=sa.text("''"), nullable=False),
        sa.Column("audio_url", sa.String(length=2048), nullable=True),
        sa.Column("media_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_moments_user_id"), "companion_moments", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_moments_occurred_at"), "companion_moments", ["occurred_at"], unique=False)
    op.create_index(op.f("ix_companion_moments_kind"), "companion_moments", ["kind"], unique=False)

    op.create_table(
        "companion_diary_entries",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("mood", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), server_default=sa.text("'nightly'"), nullable=False),
        sa.Column("memory_ids", ARRAY(sa.String()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("moment_ids", ARRAY(sa.String()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "entry_date", name="uq_companion_diary_user_date"),
    )
    op.create_index(op.f("ix_companion_diary_entries_user_id"), "companion_diary_entries", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_companion_diary_entries_entry_date"),
        "companion_diary_entries",
        ["entry_date"],
        unique=False,
    )

    op.create_table(
        "nightly_activity_logs",
        sa.Column("system_preset_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'running'"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "system_preset_id", "target_date", name="uq_nightly_activity_logs_scope_date"),
    )
    op.create_index(op.f("ix_nightly_activity_logs_user_id"), "nightly_activity_logs", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_nightly_activity_logs_target_date"),
        "nightly_activity_logs",
        ["target_date"],
        unique=False,
    )

    # 夜间能力流水线账本：capability+phase 标识能力在多阶段内的位置；log_id+action_key 联合唯一防同 capability 重复入队。
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["log_id"], ["nightly_activity_logs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("log_id", "action_key", name="uq_nightly_activity_actions_log_key"),
    )
    op.create_index(op.f("ix_nightly_activity_actions_log_id"), "nightly_activity_actions", ["log_id"], unique=False)
    op.create_index(op.f("ix_nightly_activity_actions_user_id"), "nightly_activity_actions", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_nightly_activity_actions_target_date"),
        "nightly_activity_actions",
        ["target_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_nightly_activity_actions_capability"),
        "nightly_activity_actions",
        ["capability"],
        unique=False,
    )
    op.create_index(op.f("ix_nightly_activity_actions_status"), "nightly_activity_actions", ["status"], unique=False)

    op.create_table(
        "user_model_configs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ai_config", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.alter_column("user_model_configs", "ai_config", server_default=None)
    op.create_index(op.f("ix_user_model_configs_user_id"), "user_model_configs", ["user_id"], unique=True)
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("setting_key", sa.String(length=128), nullable=False),
        sa.Column("setting_value", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "setting_key", name="uq_user_settings_user_key"),
    )
    op.create_index(op.f("ix_user_settings_setting_key"), "user_settings", ["setting_key"], unique=False)
    op.create_index(op.f("ix_user_settings_user_id"), "user_settings", ["user_id"], unique=False)
    op.create_table(
        "video_gen_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("provider_file_id", sa.String(length=128), nullable=True),
        sa.Column("file_id", sa.String(length=64), nullable=True),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_video_gen_jobs_created_at"), "video_gen_jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_provider_task_id"), "video_gen_jobs", ["provider_task_id"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_status"), "video_gen_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_user_id"), "video_gen_jobs", ["user_id"], unique=False)
    op.create_index("ix_video_gen_jobs_user_status", "video_gen_jobs", ["user_id", "status"], unique=False)
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ws_events_created_at"), "ws_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_ws_events_next_retry_at"), "ws_events", ["next_retry_at"], unique=False)
    op.create_index(op.f("ix_ws_events_status"), "ws_events", ["status"], unique=False)
    op.create_index(op.f("ix_ws_events_user_id"), "ws_events", ["user_id"], unique=False)
    op.create_index("ix_ws_events_poll", "ws_events", ["user_id", "status", "next_retry_at"], unique=False)
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("subtype", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("reasoning_content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.Text(), nullable=True),
        sa.Column("tool_call_id", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("turn_duration_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("content_type", sa.String(length=32), server_default=sa.text("'text'"), nullable=False),
        sa.Column("media_json", sa.Text(), nullable=True),
        sa.Column("summary_date", sa.String(length=10), nullable=True),
        sa.Column("draft_anchor", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("speech_style_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_messages_subtype"), "messages", ["subtype"], unique=False)
    op.create_index(op.f("ix_messages_summary_date"), "messages", ["summary_date"], unique=False)
    # IM 通道桥。conversation_id 唯一外键是「每用户每渠道一条专属 im 会话」的 DB 级锚点：
    # binding 的 (user_id, channel) 唯一性传递为渠道间不混流，UNIQUE 又阻止两条绑定共享同一会话。
    op.create_table(
        "channel_bindings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'disabled'"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("config_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("credentials", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("account_ref", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("account_name", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "channel", name="uq_channel_bindings_user_channel"),
        sa.UniqueConstraint("conversation_id", name="uq_channel_bindings_conversation_id"),
    )
    op.create_index(op.f("ix_channel_bindings_user_id"), "channel_bindings", ["user_id"], unique=False)
    op.create_index(op.f("ix_channel_bindings_status"), "channel_bindings", ["status"], unique=False)
    op.create_table(
        "channel_peers",
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("peer_id", sa.String(length=128), nullable=False),
        sa.Column("peer_name", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["binding_id"], ["channel_bindings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", "peer_id", name="uq_channel_peers_binding_peer"),
    )
    op.create_index(op.f("ix_channel_peers_binding_id"), "channel_peers", ["binding_id"], unique=False)
    op.create_index(op.f("ix_channel_peers_status"), "channel_peers", ["status"], unique=False)

    # 动态系统配置：admin UI 经此表读写并触发运行时副作用。
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("setting_key", sa.String(length=128), nullable=False),
        sa.Column("setting_value", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_system_settings_setting_key"), "system_settings", ["setting_key"], unique=True)

    # Partial unique 索引（声明式模型无法表达）。
    # 并发 POST /model 否则会留下两条 active 行。
    op.create_index(
        "uq_avatar_assets_one_active",
        "avatar_assets",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_index(
        "uq_companion_3d_models_one_active",
        "companion_3d_models",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    # 每用户一个穿着中外观；一个切分中外观（并发 confirm 的硬保证，服务层另有用户级锁）
    op.create_index(
        "uq_companion_outfits_one_active",
        "companion_outfits",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_index(
        "uq_companion_outfits_one_splitting",
        "companion_outfits",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'splitting'"),
    )
    # 每用户一条激活 2d 行：非 outfit 成功接缝与穿着翻转共用先停用后激活顺序，切分窗口内的并发激活由此兜底
    op.create_index(
        "uq_companion_2d_models_one_active",
        "companion_2d_models",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    # 每用户每预设最多一条系统预设对话：防止 ensure_system_conversations_for_user 重复插入或并发跑出多行。
    op.create_index(
        "uq_conversations_user_preset",
        "conversations",
        ["user_id", "system_preset_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'special' AND system_preset_id IS NOT NULL"),
    )
    op.create_index(
        "uq_memories_user_context",
        "memories",
        ["user_id", "system_preset_id", "context"],
        unique=True,
        postgresql_where=sa.text("context LIKE 'user_profile:%'"),
    )
    op.create_index(
        "uq_memories_diary_day",
        "memories",
        ["user_id", "system_preset_id", "context"],
        unique=True,
        postgresql_where=sa.text("context LIKE 'diary:%'"),
    )
    op.create_index(
        "ix_memories_scope_updated",
        "memories",
        ["user_id", "system_preset_id", sa.text("updated_at DESC"), sa.text("id DESC")],
    )
    for name, prefix in (
        ("uq_memories_interaction_day", "interaction_stats:"),
        ("uq_memories_nightly_actions", "recall:nightly_actions:"),
    ):
        op.create_index(
            name,
            "memories",
            ["user_id", "system_preset_id", "context"],
            unique=True,
            postgresql_where=sa.text(f"context LIKE '{prefix}%'"),
        )
    # 加速 recall consolidator 的 count-and-recent 查询。
    op.create_index(
        "ix_memories_recall_user_updated",
        "memories",
        ["user_id", sa.text("updated_at DESC")],
        unique=False,
        postgresql_where=sa.text("context LIKE 'recall:%'"),
    )

    # 向量 / trigram 检索索引。
    op.create_index(
        "ix_memories_embedding",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
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

    # Outbox 表的 LISTEN/NOTIFY 唤醒触发器（docs/ARCHITECTURE.md §5）。
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
    # 先子表再父表（messages → conversations → users）。
    # nightly_activity_actions 在 nightly_activity_logs 之后 drop（log_id FK）；system_settings 无 FK 引用，置于最末。
    for table in (
        "messages",
        "channel_peers",
        "channel_bindings",
        "ws_events",
        "video_gen_jobs",
        "user_settings",
        "user_model_configs",
        "nightly_activity_actions",
        "personas",
        "companion_moments",
        "companion_diary_entries",
        "nightly_activity_logs",
        "companion_room_backdrops",
        "memories",
        "login_records",
        "cron_jobs",
        "companion_3d_models",
        "companion_2d_models",
        "companion_outfits",
        "avatar_assets",
        "conversations",
        "users",
        "update_versions",
        "admin_sessions",
        "system_settings",
    ):
        op.drop_table(table)
