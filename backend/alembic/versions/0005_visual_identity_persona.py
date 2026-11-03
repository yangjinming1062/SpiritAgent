"""确认图身份、可选人物背景与独立后台准备；仅适用于无账户的数据库。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _require_empty_accounts() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM users) THEN RAISE EXCEPTION 'visual identity refactor requires an account-free database'; END IF; END $$"
    )


def _job_table(name: str, *columns: sa.Column | sa.UniqueConstraint) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("avatar_id", sa.BigInteger(), nullable=True),
        *columns,
        sa.PrimaryKeyConstraint("id", name=f"{name}_pkey"),
        sa.UniqueConstraint("owner_id", "id", name=f"uq_{name}_owner_id"),
        sa.ForeignKeyConstraint(
            ["owner_id", "id"], ["jobs.user_id", "jobs.id"], name=f"fk_{name}_id_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "avatar_id"],
            ["avatar_assets.user_id", "avatar_assets.id"],
            name=f"fk_{name}_avatar_id_owner",
            ondelete="SET NULL (avatar_id)",
        ),
    )
    op.create_index(f"ix_{name}_avatar_id", name, ["avatar_id"])


def _pointer(table: str, column: str, target: str, *, indexed: bool = False) -> None:
    op.add_column(table, sa.Column(column, sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        f"fk_{table}_{column}_owner",
        table,
        target,
        ["user_id", column],
        ["owner_id", "id"],
        ondelete=f"SET NULL ({column})" if table == "avatar_assets" else "CASCADE",
    )
    if indexed:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    _require_empty_accounts()
    op.drop_constraint(op.f("ck_appearance_assets_appearance_asset_owner"), "appearance_assets", type_="check")
    op.drop_column("appearance_assets", "analysis_job_id")
    op.drop_column("avatar_assets", "analysis_job_id")
    op.drop_table("character_analysis_jobs")
    op.execute("DELETE FROM jobs WHERE kind = 'character_analysis'")
    op.alter_column("avatar_assets", "revision", new_column_name="identity_revision")
    for field in ("automatic_json", "overrides_json"):
        op.drop_column("avatar_assets", field)
    op.drop_column("companion_fullbody_candidates", "body_features_json")
    op.add_column(
        "personas",
        sa.Column("onboarding_progress_json", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
    )
    _job_table(
        "appearance_preparation_jobs",
        sa.Column("preparation_id", sa.String(36), nullable=False),
        sa.Column("identity_revision", sa.Integer(), nullable=False),
        sa.Column("portrait_source_path", sa.String(2048), nullable=False),
        sa.Column("body_source_path", sa.String(2048), nullable=False),
    )
    _job_table(
        "visual_observation_jobs",
        sa.Column("identity_revision", sa.Integer(), nullable=False),
        sa.Column("portrait_source_path", sa.String(2048), nullable=False),
        sa.Column("body_source_path", sa.String(2048), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.UniqueConstraint("avatar_id", "identity_revision", name="uq_visual_observation_identity"),
    )
    _pointer("avatar_assets", "preparation_job_id", "appearance_preparation_jobs")
    _pointer("appearance_assets", "preparation_job_id", "appearance_preparation_jobs", indexed=True)
    _pointer("appearance_assets", "observation_job_id", "visual_observation_jobs", indexed=True)
    op.create_check_constraint(
        op.f("ck_appearance_assets_appearance_asset_owner"),
        "appearance_assets",
        "num_nonnulls(avatar_id, outfit_id, candidate_id, preparation_job_id, observation_job_id) = 1",
    )


def downgrade() -> None:
    _require_empty_accounts()
    op.drop_constraint(op.f("ck_appearance_assets_appearance_asset_owner"), "appearance_assets", type_="check")
    for table, column in (
        ("avatar_assets", "preparation_job_id"),
        ("appearance_assets", "preparation_job_id"),
        ("appearance_assets", "observation_job_id"),
    ):
        op.drop_column(table, column)
    op.drop_table("visual_observation_jobs")
    op.drop_table("appearance_preparation_jobs")
    op.execute("DELETE FROM jobs WHERE kind IN ('visual.observe', 'appearance.prepare')")
    op.drop_column("personas", "onboarding_progress_json")
    op.alter_column("avatar_assets", "identity_revision", new_column_name="revision")
    for field in ("automatic_json", "overrides_json"):
        op.add_column(
            "avatar_assets", sa.Column(field, postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False)
        )
    op.add_column(
        "companion_fullbody_candidates",
        sa.Column("body_features_json", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
    )
    _job_table(
        "character_analysis_jobs",
        sa.Column("extraction_id", sa.String(36), nullable=False),
        sa.Column("portrait_status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("body_status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("portrait_result_json", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("body_result_json", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("portrait_source_path", sa.String(2048), nullable=False),
        sa.Column("body_source_path", sa.String(2048), nullable=False),
    )
    _pointer("avatar_assets", "analysis_job_id", "character_analysis_jobs")
    _pointer("appearance_assets", "analysis_job_id", "character_analysis_jobs", indexed=True)
    op.create_check_constraint(
        op.f("ck_appearance_assets_appearance_asset_owner"),
        "appearance_assets",
        "num_nonnulls(avatar_id, outfit_id, candidate_id, analysis_job_id) = 1",
    )
