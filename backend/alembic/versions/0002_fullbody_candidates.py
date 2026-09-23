"""全身候选图、媒体复核与出镜身份核查字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("video_gen_jobs", sa.Column("candidate_video_url", sa.Text(), nullable=True))
    op.add_column("video_gen_jobs", sa.Column("candidate_file_id", sa.String(64), nullable=True))
    op.add_column(
        "video_gen_jobs",
        sa.Column("generation_attempt_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "companion_scenes",
        sa.Column("identity_review", sa.String(16), server_default=sa.text("'none'"), nullable=False),
    )
    op.add_column(
        "companion_scenes",
        sa.Column("identity_review_reason", sa.Text(), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "companion_action_packs",
        sa.Column("identity_review", sa.String(16), server_default=sa.text("'none'"), nullable=False),
    )
    op.add_column(
        "companion_action_packs",
        sa.Column("identity_review_reason", sa.Text(), server_default=sa.text("''"), nullable=False),
    )
    op.create_table(
        "companion_fullbody_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("avatar_id", sa.Integer(), sa.ForeignKey("avatar_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_fullbody_url", sa.String(2048), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.String(2048), nullable=False),
        sa.Column("body_features_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("body_source_hash", sa.String(64), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_companion_fullbody_candidates_user_id", "companion_fullbody_candidates", ["user_id"])
    op.create_index("ix_companion_fullbody_candidates_avatar_id", "companion_fullbody_candidates", ["avatar_id"])
    op.create_index("ix_companion_fullbody_candidates_status", "companion_fullbody_candidates", ["status"])
    op.create_table(
        "companion_media_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_type", sa.String(8), nullable=False),
        sa.Column("media_url", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("publication", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_companion_media_reviews_user_id", "companion_media_reviews", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_companion_media_reviews_user_id", table_name="companion_media_reviews")
    op.drop_table("companion_media_reviews")
    op.drop_column("video_gen_jobs", "candidate_video_url")
    op.drop_column("video_gen_jobs", "candidate_file_id")
    op.drop_column("video_gen_jobs", "generation_attempt_index")
    op.drop_index("ix_companion_fullbody_candidates_status", table_name="companion_fullbody_candidates")
    op.drop_index("ix_companion_fullbody_candidates_avatar_id", table_name="companion_fullbody_candidates")
    op.drop_index("ix_companion_fullbody_candidates_user_id", table_name="companion_fullbody_candidates")
    op.drop_table("companion_fullbody_candidates")
    op.drop_column("companion_scenes", "identity_review_reason")
    op.drop_column("companion_scenes", "identity_review")
    op.drop_column("companion_action_packs", "identity_review_reason")
    op.drop_column("companion_action_packs", "identity_review")
