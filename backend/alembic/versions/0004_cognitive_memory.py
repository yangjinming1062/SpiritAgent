"""分离认知记忆、生活检索、日记检索。"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _create_search_index(name: str, source: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source_key", sa.String(36), server_default=sa.text("(gen_random_uuid())::text"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("model_key", sa.String(64), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("embedded_hash", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=f"{name}_pkey"),
        sa.UniqueConstraint("user_id", "id", name=f"uq_{name}_owner_id"),
        sa.UniqueConstraint("user_id", "source_key", name=f"uq_{name}_source"),
        sa.UniqueConstraint("user_id", "record_id", name=f"uq_{name}_record"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name=f"{name}_user_id_fkey"),
        sa.ForeignKeyConstraint(
            ["user_id", "record_id"],
            [f"{source}.user_id", f"{source}.id"],
            ondelete="CASCADE",
            name=f"fk_{name}_record_id_owner",
        ),
        sa.CheckConstraint(
            "(embedding IS NULL AND model_key IS NULL AND dimensions IS NULL AND embedded_hash IS NULL) OR "
            "(embedding IS NOT NULL AND model_key IS NOT NULL AND dimensions IS NOT NULL AND dimensions > 0 "
            "AND embedded_hash IS NOT NULL AND vector_dims(embedding) = dimensions AND embedded_hash = content_hash)",
            name=op.f(f"ck_{name}_embedding_space"),
        ),
    )
    op.create_index(
        f"ix_{name}_text", name, ["search_text"], postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}
    )


def upgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM users)")):
        raise RuntimeError("0004 requires empty account data; delete the old account before upgrading.")
    _create_search_index("life_event_search_index", "life_events")
    _create_search_index("diary_search_index", "companion_entries")
    op.drop_constraint("fk_memories_source_event", "memories", type_="foreignkey")
    op.drop_constraint("uq_memories_source_event", "memories", type_="unique")
    op.drop_constraint(op.f("ck_memories_life_source_scope"), "memories", type_="check")
    op.drop_column("memories", "source_event_id")
    op.create_check_constraint(op.f("ck_memories_kind"), "memories", "kind IN ('recall', 'user_profile', 'reflection')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_memories_kind"), "memories", type_="check")
    op.add_column("memories", sa.Column("source_event_id", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_memories_life_source_scope"), "memories", "source_event_id IS NULL OR system_preset_id = 'companion'"
    )
    op.create_unique_constraint(
        "uq_memories_source_event", "memories", ["user_id", "system_preset_id", "source_event_id"]
    )
    op.create_foreign_key(
        "fk_memories_source_event",
        "memories",
        "life_events",
        ["user_id", "source_event_id"],
        ["user_id", "id"],
        ondelete="SET NULL (source_event_id)",
    )
    op.drop_table("diary_search_index")
    op.drop_table("life_event_search_index")
