"""结构化媒体气泡与视频原位交付。"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_gen_jobs",
        sa.Column("structured_reply", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
    )
    op.add_column("video_gen_jobs", sa.Column("media_id", sa.String(128), nullable=True))
    op.add_column("video_gen_jobs", sa.Column("reply_message_id", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_video_gen_jobs_media_id", "video_gen_jobs", ["media_id"])
    op.create_index("ix_video_gen_jobs_reply_message_id", "video_gen_jobs", ["reply_message_id"])
    op.create_foreign_key(
        "fk_video_gen_jobs_reply_message_id",
        "video_gen_jobs",
        "messages",
        ["reply_message_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_video_gen_jobs_reply_message_id", "video_gen_jobs", type_="foreignkey")
    op.drop_index("ix_video_gen_jobs_reply_message_id", table_name="video_gen_jobs")
    op.drop_constraint("uq_video_gen_jobs_media_id", "video_gen_jobs", type_="unique")
    op.drop_column("video_gen_jobs", "reply_message_id")
    op.drop_column("video_gen_jobs", "media_id")
    op.drop_column("video_gen_jobs", "structured_reply")
