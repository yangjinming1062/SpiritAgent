"""为个人成长、私下制作、生活联系及手势发言保存各自的身份。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str = "0007"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "pursuit_evidence", sa.Column("used_for_completion", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.create_table(
        "library_works",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("output_paths", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("library_works_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("library_works_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_library_works_owner_id"),
    )
    op.create_index(op.f("ix_library_works_user_id"), "library_works", ["user_id"], unique=False)
    op.create_table(
        "life_growth",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("evidence_event_id", sa.BigInteger(), nullable=False),
        sa.Column("aspect", sa.String(length=16), nullable=False),
        sa.Column("text", sa.String(length=400), nullable=False),
        sa.Column("outlook", sa.String(length=180), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("aspect IN ('interest','view','habit','boundary')", name=op.f("ck_life_growth_aspect")),
        sa.ForeignKeyConstraint(
            ["user_id", "evidence_event_id"],
            ["life_events.user_id", "life_events.id"],
            name="fk_life_growth_evidence_event_id_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("life_growth_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("life_growth_pkey")),
        sa.UniqueConstraint("user_id", "id", name="uq_life_growth_owner_id"),
    )
    op.create_index(
        "ix_life_growth_account_aspect",
        "life_growth",
        ["user_id", "aspect", sa.literal_column("updated_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.add_column("action_definitions", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_action_definitions_library_work_id_owner",
        "action_definitions",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column("companion_intents", sa.Column("life_event_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_companion_intents_life_event", "companion_intents", ["user_id", "life_event_id"])
    op.create_foreign_key(
        "fk_companion_intents_life_event_id_owner",
        "companion_intents",
        "life_events",
        ["user_id", "life_event_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_companion_intents_intent_life_source"),
        "companion_intents",
        "(source_kind IS NOT DISTINCT FROM 'life') = (life_event_id IS NOT NULL)",
    )
    op.add_column("companion_outfits", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_companion_outfits_library_work_id_owner",
        "companion_outfits",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column("companion_scenes", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_companion_scenes_library_work_id_owner",
        "companion_scenes",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column("jobs", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_jobs_library_work_id_owner",
        "jobs",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.execute("LOCK TABLE life_states IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM life_states WHERE personal_growth_json <> '{}'::jsonb) THEN
                RAISE EXCEPTION '0008 requires empty legacy personal growth records; existing records were preserved';
            END IF;
        END $$;
    """)
    op.drop_column("life_states", "personal_growth_json")
    op.add_column("life_steps", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_life_steps_library_work_id_owner",
        "life_steps",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column("media_continuations", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_media_continuations_library_work_id_owner",
        "media_continuations",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column("media_manual_operations", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_media_manual_operations_library_work_id_owner",
        "media_manual_operations",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column("messages", sa.Column("life_excerpt_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_messages_life_excerpt_id_owner",
        "messages",
        "life_events",
        ["user_id", "life_excerpt_id"],
        ["user_id", "id"],
        ondelete="SET NULL (life_excerpt_id)",
    )
    op.add_column("messages", sa.Column("gesture_request_id", sa.Uuid(), nullable=True))
    op.create_index(
        "uq_messages_gesture",
        "messages",
        ["user_id", "gesture_request_id"],
        unique=True,
        postgresql_where=sa.text("gesture_request_id IS NOT NULL"),
    )
    op.add_column("visual_items", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.drop_index(
        op.f("uq_visual_items_image_content"),
        table_name="visual_items",
        postgresql_where="((media_kind)::text = 'image'::text)",
    )
    op.create_index(
        "uq_visual_items_image_content",
        "visual_items",
        ["user_id", "content_hash", "library_work_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("media_kind = 'image'"),
    )
    op.create_foreign_key(
        "fk_visual_items_library_work_id_owner",
        "visual_items",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column("visual_versions", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_visual_versions_library_work_id_owner",
        "visual_versions",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column("ws_events", sa.Column("library_work_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_ws_events_library_work_id_owner",
        "ws_events",
        "library_works",
        ["user_id", "library_work_id"],
        ["user_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_column("pursuit_evidence", "used_for_completion")
    op.drop_constraint("fk_ws_events_library_work_id_owner", "ws_events", type_="foreignkey")
    op.drop_column("ws_events", "library_work_id")
    op.drop_constraint("fk_visual_versions_library_work_id_owner", "visual_versions", type_="foreignkey")
    op.drop_column("visual_versions", "library_work_id")
    op.drop_constraint("fk_visual_items_library_work_id_owner", "visual_items", type_="foreignkey")
    op.drop_index(
        "uq_visual_items_image_content",
        table_name="visual_items",
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("media_kind = 'image'"),
    )
    op.create_index(
        op.f("uq_visual_items_image_content"),
        "visual_items",
        ["user_id", "content_hash"],
        unique=True,
        postgresql_where="((media_kind)::text = 'image'::text)",
    )
    op.drop_column("visual_items", "library_work_id")
    op.drop_index(
        "uq_messages_gesture", table_name="messages", postgresql_where=sa.text("gesture_request_id IS NOT NULL")
    )
    op.drop_constraint("fk_messages_life_excerpt_id_owner", "messages", type_="foreignkey")
    op.drop_column("messages", "life_excerpt_id")
    op.drop_column("messages", "gesture_request_id")
    op.drop_constraint(
        "fk_media_manual_operations_library_work_id_owner", "media_manual_operations", type_="foreignkey"
    )
    op.drop_column("media_manual_operations", "library_work_id")
    op.drop_constraint("fk_media_continuations_library_work_id_owner", "media_continuations", type_="foreignkey")
    op.drop_column("media_continuations", "library_work_id")
    op.drop_constraint("fk_life_steps_library_work_id_owner", "life_steps", type_="foreignkey")
    op.drop_column("life_steps", "library_work_id")
    op.add_column(
        "life_states",
        sa.Column(
            "personal_growth_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.drop_constraint("fk_jobs_library_work_id_owner", "jobs", type_="foreignkey")
    op.drop_column("jobs", "library_work_id")
    op.drop_constraint("fk_companion_scenes_library_work_id_owner", "companion_scenes", type_="foreignkey")
    op.drop_column("companion_scenes", "library_work_id")
    op.drop_constraint("fk_companion_outfits_library_work_id_owner", "companion_outfits", type_="foreignkey")
    op.drop_column("companion_outfits", "library_work_id")
    op.drop_constraint(op.f("ck_companion_intents_intent_life_source"), "companion_intents", type_="check")
    op.drop_constraint("fk_companion_intents_life_event_id_owner", "companion_intents", type_="foreignkey")
    op.drop_constraint("uq_companion_intents_life_event", "companion_intents", type_="unique")
    op.drop_column("companion_intents", "life_event_id")
    op.drop_constraint("fk_action_definitions_library_work_id_owner", "action_definitions", type_="foreignkey")
    op.drop_column("action_definitions", "library_work_id")
    op.drop_index("ix_life_growth_account_aspect", table_name="life_growth")
    op.drop_table("life_growth")
    op.drop_index(op.f("ix_library_works_user_id"), table_name="library_works")
    op.drop_table("library_works")
