"""完整 schema 基线"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 先建扩展：memories.embedding 的 vector(1536) 须在 create_table 前存在。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "admin_sessions",
        sa.Column("token_jti", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_jti", name="uq_admin_sessions_token_jti"),
    )
    op.create_index(op.f("ix_admin_sessions_created_at"), "admin_sessions", ["created_at"], unique=False)
    op.create_index(op.f("ix_admin_sessions_is_active"), "admin_sessions", ["is_active"], unique=False)
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
        sa.Column("activation_code", sa.Text(), nullable=False),
        sa.Column("activation_token_hash", sa.String(length=128), nullable=False),
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
        sa.Column("seed_fullbody_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("is_fullbody_confirmed", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_avatar_assets_active"), "avatar_assets", ["active"], unique=False)
    op.create_index(op.f("ix_avatar_assets_user_id"), "avatar_assets", ["user_id"], unique=False)
    op.create_table(
        "companion_fullbody_candidates",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=False),
        sa.Column("base_fullbody_url", sa.String(length=2048), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.String(length=2048), nullable=False),
        sa.Column("body_features_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["avatar_id"], ["avatar_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_companion_fullbody_candidates_user_id"),
        "companion_fullbody_candidates",
        ["user_id"],
        unique=False,
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
    op.create_table(
        "companion_media_reviews",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=8), nullable=False),
        sa.Column("media_url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("publication", sa.JSON(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_media_reviews_user_id"), "companion_media_reviews", ["user_id"], unique=False)
    op.create_table(
        "companion_character_cards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("automatic_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("overrides_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("portrait_result_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("body_result_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("extraction_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("portrait_status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("body_status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("portrait_source_path", sa.String(2048), nullable=False),
        sa.Column("body_source_path", sa.String(2048), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["avatar_id"], ["avatar_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("avatar_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_companion_character_cards_user_id", "companion_character_cards", ["user_id"])
    op.create_index("ix_companion_character_cards_status", "companion_character_cards", ["status"])
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
        sa.Column("initial_video_started", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("initial_video_error", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_outfits_user_id"), "companion_outfits", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_outfits_status"), "companion_outfits", ["status"], unique=False)
    op.create_index(op.f("ix_companion_outfits_active"), "companion_outfits", ["active"], unique=False)
    # 动作库。
    op.create_table(
        "companion_action_packs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=True),
        sa.Column("outfit_id", sa.Integer(), nullable=True),
        sa.Column("pack_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("reference_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("reference_hash", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("character_snapshot", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("outfit_snapshot", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("canvas_spec", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("catalog_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("manifest_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("cover_path", sa.String(length=2048), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("identity_review", sa.String(length=16), server_default=sa.text("'none'"), nullable=False),
        sa.Column("identity_review_reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'processing'"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("appearance_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["outfit_id"], ["companion_outfits.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_action_packs_user_id"), "companion_action_packs", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_action_packs_status"), "companion_action_packs", ["status"], unique=False)
    op.create_index(op.f("ix_companion_action_packs_active"), "companion_action_packs", ["active"], unique=False)
    op.create_table(
        "companion_actions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pack_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("system_slot", sa.String(length=16), server_default=sa.text("''"), nullable=False),
        sa.Column("media_type", sa.String(length=8), server_default=sa.text("'video'"), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=True),
        sa.Column("motion_description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("use_when", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("avoid_when", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("metadata_revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("stage", sa.String(length=16), server_default=sa.text("'design'"), nullable=False),
        sa.Column("generation_state_json", sa.Text(), nullable=True),
        sa.Column("pose_generation_state_json", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("reference_hash", sa.String(length=64), nullable=True),
        sa.Column("outfit_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("artifact_path", sa.String(length=2048), nullable=True),
        sa.Column("pose_path", sa.String(length=2048), nullable=True),
        sa.Column("script_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("accepted_asset_json", sa.Text(), nullable=True),
        sa.Column("source_design_json", sa.Text(), nullable=True),  # 提案设计规格冻结。
        sa.Column("media_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("media_hash", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("target_duration_seconds", sa.Float(), nullable=True),
        sa.Column("actual_duration_ms", sa.Integer(), nullable=True),
        sa.Column("frames", sa.Integer(), nullable=True),
        sa.Column("loopable", sa.Boolean(), nullable=True),
        sa.Column("cover_path", sa.String(length=2048), nullable=True),
        sa.Column("hitmask_path", sa.String(length=2048), nullable=True),
        sa.Column("hitmask_grid_w", sa.Integer(), nullable=True),
        sa.Column("hitmask_grid_h", sa.Integer(), nullable=True),
        sa.Column("hitmask_fps", sa.Integer(), nullable=True),
        sa.Column("peek_geometry_json", sa.Text(), nullable=True),  # 探身定位，供发布与播放读取。
        sa.Column("content_rect_json", sa.Text(), nullable=True),  # 内容轮廓。
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["pack_id"], ["companion_action_packs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pack_id", "key", name="uq_companion_actions_pack_key"),
        sa.CheckConstraint("media_type IN ('image', 'video')", name="ck_companion_actions_media_type"),
        sa.CheckConstraint(
            "media_type = 'video' OR (kind IS NULL AND target_duration_seconds IS NULL "
            "AND actual_duration_ms IS NULL AND frames IS NULL AND loopable IS NULL AND hitmask_fps IS NULL)",
            name="ck_companion_actions_image_parameters",
        ),
    )
    op.create_index(
        "uq_companion_actions_pack_slot",
        "companion_actions",
        ["pack_id", "system_slot"],
        unique=True,
        postgresql_where=sa.text("system_slot <> ''"),
    )
    op.create_index(op.f("ix_companion_actions_user_id"), "companion_actions", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_actions_pack_id"), "companion_actions", ["pack_id"], unique=False)
    op.create_index(op.f("ix_companion_actions_status"), "companion_actions", ["status"], unique=False)
    op.create_table(
        "action_creations",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("creation_key", sa.String(length=96), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_id"], ["companion_actions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "creation_key", name="uq_action_creations_user_key"),
    )
    op.create_index(op.f("ix_action_creations_user_id"), "action_creations", ["user_id"], unique=False)
    op.create_index(op.f("ix_action_creations_consumed_at"), "action_creations", ["consumed_at"], unique=False)
    op.create_table(
        "action_asset_retirements",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "path", name="uq_action_asset_retirements_user_path"),
    )
    op.create_index(op.f("ix_action_asset_retirements_user_id"), "action_asset_retirements", ["user_id"], unique=False)
    op.create_table(
        "action_proposals",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pack_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), server_default=sa.text("'autonomous'"), nullable=False),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("semantic_fingerprint", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("design_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("review_decision", sa.String(length=16), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        # 制作额度按批准日（用户本地日）结算，与受理日区分。
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pack_id"], ["companion_action_packs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "source", "idempotency_key", name="uq_action_proposals_user_source_key"),
    )
    op.create_index(op.f("ix_action_proposals_user_id"), "action_proposals", ["user_id"], unique=False)
    op.create_index(op.f("ix_action_proposals_pack_id"), "action_proposals", ["pack_id"], unique=False)
    op.create_index(op.f("ix_action_proposals_status"), "action_proposals", ["status"], unique=False)
    op.create_index(
        op.f("ix_action_proposals_semantic_fingerprint"),
        "action_proposals",
        ["semantic_fingerprint"],
        unique=False,
    )
    op.create_table(
        "action_playbacks",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("play_id", sa.String(length=64), nullable=False),
        sa.Column("pack_id", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.Integer(), nullable=False),
        sa.Column("appearance_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source", sa.String(length=16), server_default=sa.text("'chat_expression'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("visible_duration_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pack_id"], ["companion_action_packs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_id"], ["companion_actions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("play_id", name="uq_action_playbacks_play_id"),
    )
    op.create_index(op.f("ix_action_playbacks_user_id"), "action_playbacks", ["user_id"], unique=False)
    op.create_index(op.f("ix_action_playbacks_pack_id"), "action_playbacks", ["pack_id"], unique=False)
    op.create_index(op.f("ix_action_playbacks_action_id"), "action_playbacks", ["action_id"], unique=False)
    op.create_index(op.f("ix_action_playbacks_status"), "action_playbacks", ["status"], unique=False)
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
        sa.Column("forked_from_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), server_default=sa.text("'standard'"), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settings_json", sa.Text(), nullable=True),
        sa.Column("is_deletable", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("is_renamable", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("is_automation", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["forked_from_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conversations_parent_id"), "conversations", ["parent_id"], unique=False)
    op.create_index(op.f("ix_conversations_forked_from_id"), "conversations", ["forked_from_id"], unique=False)
    op.create_index(op.f("ix_conversations_system_preset_id"), "conversations", ["system_preset_id"], unique=False)
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)
    op.create_table(
        "cron_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("system_preset_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("schedule", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
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
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_jti", name="uq_login_records_token_jti"),
    )
    op.create_index(op.f("ix_login_records_is_active"), "login_records", ["is_active"], unique=False)
    op.create_index(op.f("ix_login_records_login_at"), "login_records", ["login_at"], unique=False)
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
        "companion_scenes",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("origin", sa.String(length=16), server_default=sa.text("'user_request'"), nullable=False),
        # generated=AI 生成；user_upload=用户自备图（pending 回传行不参与生成恢复）。
        sa.Column("source", sa.String(length=16), server_default=sa.text("'generated'"), nullable=False),
        sa.Column("stage", sa.String(length=24), server_default=sa.text("'prepare'"), nullable=False),
        sa.Column("target_size_json", sa.Text(), nullable=True),
        sa.Column("source_size_json", sa.Text(), nullable=True),
        sa.Column("image_size_json", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=80), server_default=sa.text("''"), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("requirements", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("auto_activate", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("switch_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("generation_state_json", sa.Text(), nullable=True),
        sa.Column("regeneration_status", sa.String(length=24), nullable=True),  # 独立重生成任务状态。
        sa.Column("regeneration_stage", sa.String(length=24), nullable=True),
        sa.Column("regeneration_error", sa.Text(), nullable=True),
        sa.Column("regeneration_task_id", sa.String(length=36), nullable=True),
        sa.Column("regeneration_state_json", sa.Text(), nullable=True),
        sa.Column("reference_image", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("prompt", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("media_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_scenes_user_id"), "companion_scenes", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_scenes_status"), "companion_scenes", ["status"], unique=False)
    op.create_index(
        op.f("ix_companion_scenes_regeneration_status"),
        "companion_scenes",
        ["regeneration_status"],
        unique=False,
    )
    op.create_table(
        "scene_display_targets",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scene_display_targets_user_id"), "scene_display_targets", ["user_id"], unique=True)
    op.create_table(
        "scene_generation_attempts",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["companion_scenes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_scene_generation_attempts_user_id"),
        "scene_generation_attempts",
        ["user_id"],
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
        sa.Column("active_scene_id", sa.Integer(), nullable=True),
        sa.Column("scene_switch_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("scene_state_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("scene_policy", sa.String(length=16), server_default=sa.text("'llm_may_replace'"), nullable=False),
        sa.Column("outfit_policy", sa.String(length=16), server_default=sa.text("'llm_may_replace'"), nullable=False),
        sa.Column("current_mood", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["active_scene_id"], ["companion_scenes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_personas_is_complete"), "personas", ["is_complete"], unique=False)
    op.create_index(op.f("ix_personas_is_portrait_confirmed"), "personas", ["is_portrait_confirmed"], unique=False)
    op.create_index(op.f("ix_personas_user_id"), "personas", ["user_id"], unique=True)
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
        sa.Column("quota_kind", sa.String(length=24), nullable=False),
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
    op.create_index(op.f("ix_companion_posts_quota_kind"), "companion_posts", ["quota_kind"], unique=False)
    op.create_index(op.f("ix_companion_posts_user_id"), "companion_posts", ["user_id"], unique=False)
    op.create_index(
        "ix_companion_posts_unread_user",
        "companion_posts",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("is_read IS FALSE"),
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
        "post_publications",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("trigger", sa.String(length=24), nullable=False),
        sa.Column("quota_kind", sa.String(length=24), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("progress_json", sa.JSON(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("post_publications_pkey")),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_post_publication_request"),
    )
    op.create_index(op.f("ix_post_publications_reserved_at"), "post_publications", ["reserved_at"], unique=False)
    op.create_index(op.f("ix_post_publications_status"), "post_publications", ["status"], unique=False)
    op.create_index(op.f("ix_post_publications_user_id"), "post_publications", ["user_id"], unique=False)
    op.create_table(
        "companion_diary_entries",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("mood", sa.String(length=32), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("post_ids", ARRAY(sa.String()), server_default=sa.text("'{}'"), nullable=False),
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
    op.create_index(
        "ix_companion_diary_unread_user",
        "companion_diary_entries",
        ["user_id"],
        postgresql_where=sa.text("is_read IS FALSE"),
    )
    # 待兑现的陪伴意图：等待、认领（租约）与交付状态和模型调用生命周期分离。
    op.create_table(
        "companion_intents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_intents_user_id"), "companion_intents", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_intents_status"), "companion_intents", ["status"], unique=False)
    op.create_index(op.f("ix_companion_intents_wake_at"), "companion_intents", ["wake_at"], unique=False)
    op.create_index(op.f("ix_companion_intents_expires_at"), "companion_intents", ["expires_at"], unique=False)
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "target_date", name="uq_nightly_activity_logs_user_date"),
    )
    op.create_index(op.f("ix_nightly_activity_logs_user_id"), "nightly_activity_logs", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_nightly_activity_logs_target_date"),
        "nightly_activity_logs",
        ["target_date"],
        unique=False,
    )
    # 夜间能力流水线账本：capability+phase 定位阶段；log_id+action_key 防同 capability 重复入队。
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
        sa.Column("structured_reply", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("media_id", sa.String(length=128), nullable=True),
        sa.Column("reply_message_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("generation_state_json", sa.Text(), nullable=False),
        sa.Column("generation_attempt_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("candidate_video_url", sa.Text(), nullable=True),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_id", name="uq_video_gen_jobs_media_id"),
    )
    op.create_index(op.f("ix_video_gen_jobs_created_at"), "video_gen_jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_provider_task_id"), "video_gen_jobs", ["provider_task_id"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_status"), "video_gen_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_user_id"), "video_gen_jobs", ["user_id"], unique=False)
    op.create_index("ix_video_gen_jobs_user_status", "video_gen_jobs", ["user_id", "status"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_reply_message_id"), "video_gen_jobs", ["reply_message_id"], unique=False)
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
        sa.Column("summary_through_message_id", sa.Integer(), nullable=True),
        # IM 入站先落库再确认；context_order 表达 queued 批排序，dedup_key 做渠道重投去重。
        sa.Column("queued", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("channel_peer_id", sa.String(length=128), nullable=True),
        sa.Column("discarded", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("dedup_key", sa.String(length=64), nullable=True),
        sa.Column("context_order", sa.Integer(), nullable=True),
        sa.Column("reply_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.CheckConstraint(
            "content_type IN ('text', 'multimodal_v1', 'companion_reply')",
            name="ck_messages_content_type",
        ),
        sa.CheckConstraint(
            "(content_type = 'companion_reply') = (reply_json IS NOT NULL)",
            name="ck_messages_reply_delivery",
        ),
        sa.CheckConstraint(
            "content_type != 'companion_reply' OR (role = 'assistant' AND content IS NOT NULL AND tool_calls IS NULL)",
            name="ck_messages_reply_content",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_messages_dedup_key"),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_messages_subtype"), "messages", ["subtype"], unique=False)
    # video_gen_jobs 建表早于 messages，回绑外键在此补齐。
    op.create_foreign_key(
        "video_gen_jobs_reply_message_id_fkey",
        "video_gen_jobs",
        "messages",
        ["reply_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # IM 通道桥：conversation_id 唯一外键锚定「每用户每渠道一条专属 im 会话」，防渠道间混流与绑定共享会话。
    op.create_table(
        "channel_bindings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'disabled'"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
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
        sa.Column("authorization_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
    # 渠道待补发队列：执行与投递独立，恢复语义见 docs/PROTOCOL.md「IM 通道」。
    op.create_table(
        "channel_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("peer_id", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["binding_id"], ["channel_bindings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_channel_deliveries_binding_id"), "channel_deliveries", ["binding_id"], unique=False)
    op.create_index(op.f("ix_channel_deliveries_status"), "channel_deliveries", ["status"], unique=False)

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

    # Partial unique 索引：并发生成或切换头像不留下两条 active 行。
    op.create_index(
        "uq_avatar_assets_one_active",
        "avatar_assets",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    # 每用户一个激活中外观（并发 confirm 的硬保证，服务层另有用户级锁）。
    op.create_index(
        "uq_companion_outfits_one_active",
        "companion_outfits",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    # 每用户一个激活动作包：先停用后激活的翻转由此兜底。
    op.create_index(
        "uq_companion_action_packs_one_active",
        "companion_action_packs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    # 每用户每预设最多一条系统预设对话：防 ensure_system_conversations_for_user 并发重复插入。
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
        ("uq_memories_reflection_slot", "reflection:"),
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
    # 召回记忆（context LIKE 'recall:%'）的最近条目读取与定时维护扫描。
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

    # Outbox 表的 LISTEN/NOTIFY 唤醒触发器（docs/ARCHITECTURE.md「调度与异步交付」）。
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
    # drop 顺序按外键：子表先于父表；channel_peers/deliveries 后于 bindings；actions/packs 先于 outfits；fullbody 先于 avatar；actions 后于 logs；system_settings 最末。
    for table in (
        "video_gen_jobs",
        "messages",
        "channel_deliveries",
        "channel_peers",
        "channel_bindings",
        "ws_events",
        "user_settings",
        "user_model_configs",
        "nightly_activity_actions",
        "personas",
        "post_publications",
        "companion_post_comments",
        "companion_posts",
        "companion_diary_entries",
        "companion_intents",
        "nightly_activity_logs",
        "scene_generation_attempts",
        "scene_display_targets",
        "companion_scenes",
        "memories",
        "login_records",
        "cron_jobs",
        "action_playbacks",
        "action_asset_retirements",
        "action_creations",
        "action_proposals",
        "companion_actions",
        "companion_action_packs",
        "companion_outfits",
        "companion_character_cards",
        "companion_media_reviews",
        "companion_fullbody_candidates",
        "avatar_assets",
        "conversations",
        "users",
        "update_versions",
        "admin_sessions",
        "system_settings",
    ):
        op.drop_table(table)
