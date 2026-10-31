"""重置生活历史并建立面向用户的生活叙事契约。"""

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 只失效被重置生活事实派生的记忆投影；其余记忆及其审计历史保留。
    op.execute(
        sa.text(
            "UPDATE memories SET status = 'invalidated' WHERE source_event_id IN (SELECT id FROM life_events) AND status IN ('active', 'candidate')"
        )
    )
    # 旧记录混合执行日志与面向用户的叙述，按约定直接废弃；独立的额度账本保留，
    # 已提交的付费请求与当日额度不因删除历史而被重置。
    for table in (
        "pursuit_evidence",
        "life_events",
        "life_states",
        "life_steps",
        "life_pursuits",
    ):
        op.execute(sa.text(f"DELETE FROM {table}"))

    # 旧计划不满足新的叙事契约；在此直接终止，不在运行时重建伙伴表达。
    op.execute(
        sa.text(
            "UPDATE nightly_activity_actions SET status = 'interrupted', "
            'result = \'{"status":"interrupted","reason":"生活数据已重置，旧计划不再执行"}\'::jsonb '
            "WHERE status IN ('pending', 'running')"
        )
    )

    op.add_column("life_states", sa.Column("narrative", sa.Text(), nullable=True))
    op.add_column("life_pursuits", sa.Column("narrative", sa.Text(), nullable=True))
    op.add_column("life_steps", sa.Column("narrative", sa.Text(), nullable=True))
    op.add_column("life_steps", sa.Column("failure_code", sa.String(length=64), nullable=True))
    op.add_column("life_events", sa.Column("narrative", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("life_events", "narrative")
    op.drop_column("life_steps", "failure_code")
    op.drop_column("life_steps", "narrative")
    op.drop_column("life_pursuits", "narrative")
    op.drop_column("life_states", "narrative")
