"""派生来源与子 Agent 会话归属分列。"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("forked_from_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_conversations_forked_from_id"), "conversations", ["forked_from_id"], unique=False)
    op.create_foreign_key(
        "conversations_forked_from_id_fkey",
        "conversations",
        "conversations",
        ["forked_from_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # 旧数据的派生会话与子 Agent 会话都记在 parent_id。子 Agent 会话创建时标题恒为 "Subagent Task"，
    # 首条用户消息是委派说明（历代前缀如下）；两项都不符的子会话按派生会话迁出。已迁出的行不再匹配，可重复执行。
    op.execute("""
UPDATE conversations AS c
SET forked_from_id = c.parent_id, parent_id = NULL
WHERE c.parent_id IS NOT NULL
  AND c.title <> 'Subagent Task'
  AND NOT EXISTS (
    SELECT 1
    FROM (
      SELECT m.content
      FROM messages AS m
      WHERE m.conversation_id = c.id AND m.role = 'user'
      ORDER BY m.id
      LIMIT 1
    ) AS first_user
    WHERE starts_with(first_user.content, '[INTERNAL DELEGATION')
       OR starts_with(first_user.content, 'You are a subagent delegated')
  )
""")


def downgrade() -> None:
    # 派生来源写回 parent_id：派生会话重新随来源级联删除，并在会话列表中隐藏；来源已删除的派生会话保持独立。
    op.execute("""
UPDATE conversations
SET parent_id = forked_from_id
WHERE forked_from_id IS NOT NULL AND parent_id IS NULL
""")
    op.drop_constraint("conversations_forked_from_id_fkey", "conversations", type_="foreignkey")
    op.drop_index(op.f("ix_conversations_forked_from_id"), table_name="conversations")
    op.drop_column("conversations", "forked_from_id")
