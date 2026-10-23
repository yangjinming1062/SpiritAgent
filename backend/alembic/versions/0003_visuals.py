"""影像库：组合成品替代桌景套装，动作改为独立素材。

数据转换规则与只读预检共用 workflows/maintenance/visuals_migration.py：桌景套装的成品与
暂停制作转为影像成品与制作工作项，预设动作与自定义定义转为独立动作素材，呈现状态指向新
影像。一次性来源补全的生产 ID 只存在于本转换段。迁移不调用模型、不重新生成媒体、不搬动或
重编码现有成品；缺失文件在写入前直接失败，回滚使用升级前完整备份。
"""

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from features.visuals.schemas import ComboSnapshot, VisualJobProgress
from infrastructure.config import SETTINGS
from workflows.maintenance.visuals_migration import (
    RETIRED_NIGHTLY_CAPABILITIES,
    build_plan,
    fetch_old_rows,
    public_rows,
    resolve_action_ids,
)
from workflows.maintenance.visuals_migration_files import verify_migration_files

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTION_COLUMNS = (
    "user_id",
    "name",
    "normalized_name",
    "motion_description",
    "use_when_json",
    "avoid_when_json",
    "allowed_triggers_json",
    "clip_kind",
    "duration_seconds",
    "reason",
    "source",
    "status",
    "review_reason",
    "review_source_json",
)

_ITEM_COLUMNS = (
    "user_id",
    "media_kind",
    "title",
    "status",
    "media_path",
    "poster_path",
    "width",
    "height",
    "duration_ms",
    "content_hash",
    "description",
    "outfit_description",
    "snapshot_json",
    "version",
    "outfit_id",
    "scene_id",
    "action_id",
    "input_image_id",
    "input_image_path",
    "input_image_hash",
)

_JOB_COLUMNS = (
    "user_id",
    "target_visual_id",
    "purpose",
    "target_kind",
    "expected_version",
    "snapshot_json",
    "feedback",
    "video_feedback",
    "state",
    "stage",
    "progress_json",
    "candidates_json",
    "repair_count",
    "repair_limit",
    "error",
    "result_visual_id",
    "result_image_id",
)

_PRESENTATION_COLUMNS = (
    "user_id",
    "current_visual_id",
    "loop_visual_id",
    "pinned",
    "autonomous_enabled",
    "target_width",
    "target_height",
    "presentation_epoch",
    "state_revision",
    "pending_command_json",
)


def _timestamps() -> sa.Column:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def _create_visual_tables() -> None:
    op.create_table(
        "visual_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("media_kind", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'ready'")),
        sa.Column("media_path", sa.Text(), nullable=False),
        sa.Column("poster_path", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("outfit_description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("outfit_id", sa.Integer(), nullable=True),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("input_image_id", sa.Integer(), nullable=True),
        sa.Column("input_image_path", sa.Text(), nullable=True),
        sa.Column("input_image_hash", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="visual_items_user_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["outfit_id"],
            ["companion_outfits.id"],
            name="visual_items_outfit_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["companion_scenes.id"],
            name="visual_items_scene_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["action_definitions.id"],
            name="visual_items_action_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["input_image_id"],
            ["visual_items.id"],
            name="visual_items_input_image_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="visual_items_pkey"),
    )
    op.create_index("ix_visual_items_user_id", "visual_items", ["user_id"])
    op.create_index("ix_visual_items_media_kind", "visual_items", ["media_kind"])
    op.create_index("ix_visual_items_outfit_id", "visual_items", ["outfit_id"])
    op.create_index("ix_visual_items_scene_id", "visual_items", ["scene_id"])
    op.create_index("ix_visual_items_action_id", "visual_items", ["action_id"])
    op.create_index(
        "uq_visual_items_image_content",
        "visual_items",
        ["user_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("media_kind = 'image'"),
    )

    op.create_table(
        "visual_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_visual_id", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False, server_default=sa.text("'create'")),
        sa.Column("target_kind", sa.String(length=8), nullable=False, server_default=sa.text("'video'")),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("video_feedback", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("state", sa.String(length=24), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("stage", sa.String(length=24), nullable=False, server_default=sa.text("'prepare'")),
        sa.Column("progress_json", sa.Text(), nullable=True),
        sa.Column("candidates_json", sa.Text(), nullable=True),
        sa.Column("repair_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("repair_limit", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_visual_id", sa.Integer(), nullable=True),
        sa.Column("result_image_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="visual_jobs_user_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["target_visual_id"],
            ["visual_items.id"],
            name="visual_jobs_target_visual_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["result_visual_id"],
            ["visual_items.id"],
            name="visual_jobs_result_visual_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["result_image_id"],
            ["visual_items.id"],
            name="visual_jobs_result_image_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="visual_jobs_pkey"),
    )
    op.create_index("ix_visual_jobs_user_id", "visual_jobs", ["user_id"])
    op.create_index("ix_visual_jobs_target_visual_id", "visual_jobs", ["target_visual_id"])
    op.create_index("ix_visual_jobs_state", "visual_jobs", ["state"])

    op.create_table(
        "visual_presentations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("current_visual_id", sa.Integer(), nullable=True),
        sa.Column("loop_visual_id", sa.Integer(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("autonomous_enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("target_width", sa.Integer(), nullable=True),
        sa.Column("target_height", sa.Integer(), nullable=True),
        sa.Column("presentation_epoch", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pending_command_json", sa.Text(), nullable=True),
        sa.Column("current_snapshot_json", sa.Text(), nullable=True),
        sa.Column("loop_snapshot_json", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="visual_presentations_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["current_visual_id"],
            ["visual_items.id"],
            name="visual_presentations_current_visual_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["loop_visual_id"],
            ["visual_items.id"],
            name="visual_presentations_loop_visual_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="visual_presentations_pkey"),
    )
    op.create_index("ix_visual_presentations_user_id", "visual_presentations", ["user_id"], unique=True)

    op.create_table(
        "visual_playbacks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("visual_id", sa.Integer(), nullable=False),
        sa.Column("visual_version", sa.Integer(), nullable=False),
        sa.Column("play_id", sa.String(length=36), nullable=False),
        sa.Column("presentation_epoch", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("client_id", sa.String(length=80), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_kind", sa.String(length=24), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="visual_playbacks_user_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["visual_id"],
            ["visual_items.id"],
            name="visual_playbacks_visual_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="visual_playbacks_pkey"),
        sa.UniqueConstraint("play_id", name="uq_visual_playbacks_play_id"),
    )
    op.create_index("ix_visual_playbacks_user_id", "visual_playbacks", ["user_id"])
    op.create_index("ix_visual_playbacks_visual_id", "visual_playbacks", ["visual_id"])


def _write_plan(bind, plan) -> None:  # noqa: ANN001 -- 迁移内省略连接类型标注
    real_item_ids: list[int] = []

    def real_item(value):
        return real_item_ids[value] if isinstance(value, int) else value

    for raw in plan.visual_items:
        row = public_rows([raw])[0]
        # 临时索引先换真实 ID 再插入，避免自引用外键在写入瞬间不成立。
        row["input_image_id"] = real_item(row.get("input_image_id"))
        columns = ", ".join(_ITEM_COLUMNS)
        values = ", ".join(f":{name}" for name in _ITEM_COLUMNS)
        new_id = bind.execute(
            sa.text(f"INSERT INTO visual_items ({columns}) VALUES ({values}) RETURNING id"),
            {name: row.get(name) for name in _ITEM_COLUMNS},
        ).scalar_one()
        real_item_ids.append(new_id)

    real_job_ids: list[int] = []
    for raw in plan.visual_jobs:
        row = public_rows([raw])[0]
        row["target_visual_id"] = real_item(row.get("target_visual_id"))
        row["result_visual_id"] = real_item(row.get("result_visual_id"))
        row["result_image_id"] = real_item(row.get("result_image_id"))
        columns = ", ".join(_JOB_COLUMNS)
        values = ", ".join(f":{name}" for name in _JOB_COLUMNS)
        new_id = bind.execute(
            sa.text(f"INSERT INTO visual_jobs ({columns}) VALUES ({values}) RETURNING id"),
            {name: row.get(name) for name in _JOB_COLUMNS},
        ).scalar_one()
        real_job_ids.append(new_id)

    for raw in plan.presentation_rows:
        row = dict(raw)
        row["current_visual_id"] = real_item(row.get("current_visual_id"))
        row["loop_visual_id"] = real_item(row.get("loop_visual_id"))
        columns = ", ".join(_PRESENTATION_COLUMNS)
        values = ", ".join(f":{name}" for name in _PRESENTATION_COLUMNS)
        bind.execute(
            sa.text(f"INSERT INTO visual_presentations ({columns}) VALUES ({values})"),
            {name: row.get(name) for name in _PRESENTATION_COLUMNS},
        )

    for entry in plan.queue_retargets:
        if entry["entity_id"] is None:
            bind.execute(
                sa.text(
                    "DELETE FROM video_queue_requests WHERE id = :id AND state IN ('succeeded', 'failed', 'cancelled', 'stopped')",
                ),
                {"id": entry["id"]},
            )
            continue
        bind.execute(
            sa.text(
                "UPDATE video_queue_requests SET kind = 'visuals', entity_id = :entity_id, "
                "frozen_json = :frozen_json, revision = revision + 1, updated_at = now() WHERE id = :id",
            ),
            {
                "entity_id": real_job_ids[entry["entity_id"]] if entry["entity_id"] is not None else None,
                "frozen_json": entry["frozen_json"],
                "id": entry["id"],
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    rows = fetch_old_rows(bind)
    plan = build_plan(rows, Path(SETTINGS.data_dir))
    checks = verify_migration_files(plan, Path(SETTINGS.data_dir))
    failures = [entry for entry in checks if entry["error"]]
    if failures or plan.diagnostics.get("pending_queue_requests"):
        raise RuntimeError("影像迁移文件核验失败或存在未决供应商提交，请先运行只读预检")
    missing = plan.diagnostics.get("missing_files") or []
    if missing:
        raise RuntimeError(f"影像引用的媒体文件缺失，拒绝转换：{missing}")
    for item in plan.visual_items:
        ComboSnapshot.model_validate_json(item["snapshot_json"])
    for job in plan.visual_jobs:
        if job.get("progress_json"):
            VisualJobProgress.model_validate_json(job["progress_json"])

    _create_visual_tables()
    op.execute(sa.text("ALTER TABLE action_definitions ADD COLUMN allowed_triggers_json TEXT"))
    op.execute(
        sa.text(
            "UPDATE action_definitions SET allowed_triggers_json = '[]' WHERE allowed_triggers_json IS NULL",
        ),
    )
    op.execute(
        sa.text(
            'ALTER TABLE action_definitions ALTER COLUMN allowed_triggers_json SET DEFAULT \'["chat", "manual"]\'',
        ),
    )
    op.execute(sa.text("ALTER TABLE action_definitions ALTER COLUMN allowed_triggers_json SET NOT NULL"))
    op.create_index("ix_action_definitions_normalized_name", "action_definitions", ["normalized_name"])
    # 旧列在写入新动作素材前先退役：新登记的行不再携带来源键与目标信息。
    op.drop_constraint("uq_action_definition_name", "action_definitions", type_="unique")
    op.drop_column("action_definitions", "outfit_id")
    op.drop_column("action_definitions", "source_visual_key")
    op.drop_column("action_definitions", "origin_target_id")
    op.drop_column("action_definitions", "reuse_definition_id")
    op.drop_column("action_definitions", "fanout_pending")

    placeholder: dict[int, int] = {}
    # 先登记动作素材，再按实际 ID 回填影像与快照中的动作引用。
    inserted = _write_action_rows(bind, plan)
    placeholder.update(inserted)
    resolve_action_ids(plan, placeholder)
    _write_plan(bind, plan)

    # 退役旧结构：先子表后父表；配置与夜间计划随后收口。
    op.drop_table("desktop_video_playbacks")
    op.drop_table("desktop_video_states")
    op.drop_table("desktop_video_actions")
    op.drop_table("desktop_video_sets")
    op.drop_table("action_sync_targets")
    op.drop_index("uq_companion_outfits_one_active", table_name="companion_outfits")
    op.drop_column("companion_outfits", "active")
    op.drop_column("companion_scenes", "auto_activate")
    op.drop_column("companion_scenes", "switch_version")
    op.drop_column("companion_scenes", "activated_at")
    op.drop_column("personas", "active_scene_id")
    op.drop_column("personas", "scene_switch_version")
    op.drop_column("personas", "scene_state_version")

    op.execute(
        sa.text(
            r"""
        UPDATE user_model_configs
        SET ai_config = (jsonb_set(ai_config::jsonb, '{tasks,visuals}',
            COALESCE(ai_config::jsonb #> '{tasks,desktop_life}', 'null'::jsonb)) #- '{tasks,desktop_life}')::json
        WHERE ai_config::jsonb #> '{tasks,desktop_life}' IS NOT NULL;
        UPDATE system_settings
        SET setting_value = (jsonb_set(setting_value::jsonb, '{tasks,visuals}',
            COALESCE(setting_value::jsonb #> '{tasks,desktop_life}', 'null'::jsonb)) #- '{tasks,desktop_life}')::text
        WHERE setting_key = 'ai_config' AND setting_value::jsonb #> '{tasks,desktop_life}' IS NOT NULL;

        DELETE FROM ws_events WHERE event_type LIKE 'companion.desktop_video.%';
        UPDATE video_queue_requests SET frozen_json = (frozen_json::jsonb - 'task')::text
        WHERE kind = 'visuals' AND frozen_json::jsonb ->> 'task' = 'desktop_life';
            """,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE nightly_activity_actions "
            "SET status = 'failed', "
            "result = (COALESCE(result, '{}'::json)::jsonb || '{\"reason\": \"旧呈现操作已退役\"}'::jsonb)::json "
            "WHERE status = 'pending' AND capability IN :retired_capabilities",
        ).bindparams(sa.bindparam("retired_capabilities", expanding=True)),
        {"retired_capabilities": list(RETIRED_NIGHTLY_CAPABILITIES)},
    )


def _write_action_rows(bind, plan) -> dict[int, int]:  # noqa: ANN001
    inserted: dict[int, int] = {}
    for entry in plan.action_assets:
        existing = entry.get("_existing_id")
        row = public_rows([entry])[0]
        if existing:
            bind.execute(
                sa.text(
                    "UPDATE action_definitions SET name = :name, normalized_name = :normalized_name, "
                    "motion_description = :motion_description, use_when_json = :use_when_json, "
                    "avoid_when_json = :avoid_when_json, allowed_triggers_json = :allowed_triggers_json, "
                    "clip_kind = :clip_kind, duration_seconds = :duration_seconds, reason = :reason, "
                    "source = :source, status = :status, review_reason = :review_reason, "
                    "review_source_json = :review_source_json WHERE id = :id",
                ),
                {k: row[k] for k in _ACTION_COLUMNS} | {"id": existing},
            )
            inserted[entry["_index"]] = existing
        else:
            columns = ", ".join(_ACTION_COLUMNS)
            values = ", ".join(f":{name}" for name in _ACTION_COLUMNS)
            new_id = bind.execute(
                sa.text(f"INSERT INTO action_definitions ({columns}) VALUES ({values}) RETURNING id"),
                {k: row[k] for k in _ACTION_COLUMNS},
            ).scalar_one()
            inserted[entry["_index"]] = new_id
    dropped = plan.diagnostics.get("dropped_definition_ids") or []
    if dropped:
        bind.execute(
            sa.text("DELETE FROM action_definitions WHERE id = ANY(:ids)"),
            {"ids": list(dropped)},
        )
    return inserted


def downgrade() -> None:
    raise RuntimeError("桌景套装数据及旧结构已退役；恢复旧版本须使用升级前的完整备份")
