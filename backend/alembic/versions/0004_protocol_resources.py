"""资源契约升级与媒体复核数据规范化。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from infrastructure.upgrade_checks import require_video_queue_upgrade_ready

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    require_video_queue_upgrade_ready(connection)
    reviews = sa.table("companion_media_reviews", sa.column("id", sa.Integer), sa.column("publication", sa.JSON))
    slots = {"idle", "drag", "walk_left", "walk_right", "peek_left", "peek_right"}
    for row in connection.execute(sa.select(reviews.c.id, reviews.c.publication)).mappings():
        publication = row["publication"]
        if publication is None or "system_slot" in publication:
            continue
        publication = dict(publication)
        title = publication.get("title", "")
        publication["system_slot"] = title if title in slots else ""
        if title in slots:
            publication["title"] = ""
        connection.execute(sa.update(reviews).where(reviews.c.id == row["id"]).values(publication=publication))


def downgrade() -> None:
    """无结构变更；保留已明确的动作槽位。"""
