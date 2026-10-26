"""Stable built-in actions and explicit review failures.

Revision ID: 0002
Revises: 0001
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_PRESETS = (
    {
        "name": "安静相伴",
        "motion_description": "保持有稳定支撑的放松姿态安静相伴，头肩、躯干与重心安稳；呼吸仅有几乎不可见的局部变化，偶发眨眼也可不眨。",
        "use_when": ["默认陪伴", "短动作结束后返回"],
        "avoid_when": [],
        "allowed_triggers": ["periodic", "manual"],
        "clip_kind": "loop",
        "duration_seconds": 4,
        "reason": "",
        "key": "idle",
    },
    {
        "name": "专注做事",
        "motion_description": "明确从事一件适合自身身体、性格与环境的事，选定阅读、书写或操作电脑等一种活动并持续专注。",
        "use_when": ["进入工作状态", "继续自己的日常活动"],
        "avoid_when": ["已确认的起始画面缺少活动必需的物品"],
        "allowed_triggers": ["periodic", "manual"],
        "clip_kind": "loop",
        "duration_seconds": 6,
        "reason": "",
        "key": "focus",
    },
    {
        "name": "闭目小憩",
        "motion_description": "进入可辨认的舒服休息姿态，放松并闭目小憩，保持该休息状态。",
        "use_when": ["安静时段", "休息情境"],
        "avoid_when": ["身体结构不适合闭目"],
        "allowed_triggers": ["periodic", "manual"],
        "clip_kind": "loop",
        "duration_seconds": 6,
        "reason": "",
        "key": "relax",
    },
    {
        "name": "注意到你",
        "motion_description": "暂停手边活动，将注意力转向观看者，短暂招呼后恢复原来的姿态与注意力。",
        "use_when": ["用户恢复操作", "开始交流"],
        "avoid_when": ["仅因时间经过而没有交流或返回情境"],
        "allowed_triggers": ["user_return", "chat", "manual"],
        "clip_kind": "once",
        "duration_seconds": 5,
        "reason": "",
        "key": "look",
    },
    {
        "name": "认可鼓励",
        "motion_description": "用符合自身身体与性格的明确神态或身体表达认可与鼓励，表达后恢复原姿态。",
        "use_when": ["对话中值得肯定的进展", "完成或鼓励情境"],
        "avoid_when": ["没有当前回应情境"],
        "allowed_triggers": ["chat", "manual"],
        "clip_kind": "once",
        "duration_seconds": 5,
        "reason": "",
        "key": "smile",
    },
    {
        "name": "舒展身体",
        "motion_description": "从原姿态完成一次过程清楚的身体舒展，幅度适合已有身体结构，结束后恢复舒服原姿态。",
        "use_when": ["长时间保持同一状态"],
        "avoid_when": ["身体或空间不能支持舒展"],
        "allowed_triggers": ["periodic", "manual"],
        "clip_kind": "once",
        "duration_seconds": 8,
        "reason": "",
        "key": "stretch",
    },
    {
        "name": "喝口水",
        "motion_description": "从原姿态拿起已有杯子、饮用、放回原处，并恢复原姿态，完成能看懂的日常活动。",
        "use_when": ["伙伴自己的日常活动"],
        "avoid_when": ["没有杯子", "身体结构不支持拿取或饮用"],
        "allowed_triggers": ["periodic", "manual"],
        "clip_kind": "once",
        "duration_seconds": 8,
        "reason": "",
        "key": "drink",
    },
    {
        "name": "整理仪容",
        "motion_description": "整理衣袖、头发或自身适用的一个部位，完成后恢复原来的姿态与物品位置。",
        "use_when": ["低频日常活动"],
        "avoid_when": ["没有可整理的部位或造型"],
        "allowed_triggers": ["periodic", "manual"],
        "clip_kind": "once",
        "duration_seconds": 6,
        "reason": "",
        "key": "groom",
    },
    {
        "name": "跳一小段",
        "motion_description": "从原姿态起身或进入适合自身身体的舞姿，完成一小段清楚的舞蹈，再恢复原位和原姿态。",
        "use_when": ["用户点播", "当前对话明确邀请"],
        "avoid_when": ["没有当前邀请", "空间或身体结构不支持舞蹈"],
        "allowed_triggers": ["chat", "manual"],
        "clip_kind": "once",
        "duration_seconds": 12,
        "reason": "",
        "key": "dance",
    },
)


def upgrade() -> None:
    op.add_column("action_definitions", sa.Column("preset_key", sa.String(24), nullable=True))
    op.create_unique_constraint("uq_action_definitions_preset", "action_definitions", ["user_id", "preset_key"])
    actions = sa.table(
        "action_definitions",
        sa.column("id", sa.BigInteger()),
        sa.column("user_id", sa.BigInteger()),
        sa.column("preset_key", sa.String()),
        sa.column("source", sa.String()),
        sa.column("name", sa.String()),
        sa.column("normalized_name", sa.String()),
        sa.column("motion_description", sa.Text()),
        sa.column("clip_kind", sa.String()),
        sa.column("duration_seconds", sa.Integer()),
        sa.column("reason", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("review_reason", sa.Text()),
        sa.column("use_when_json", postgresql.JSONB()),
        sa.column("avoid_when_json", postgresql.JSONB()),
        sa.column("allowed_triggers_json", postgresql.JSONB()),
        sa.column("review_source_json", postgresql.JSONB()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    users = sa.table("users", sa.column("id", sa.BigInteger()))
    for preset in _PRESETS:
        values = {name: preset[name] for name in ("name", "motion_description", "clip_kind", "duration_seconds")}
        values.update(
            preset_key=preset["key"],
            source="preset",
            normalized_name=preset["name"].casefold(),
            status="approved",
            reason="",
            review_reason="",
        )
        for key in ("use_when", "avoid_when", "allowed_triggers"):
            values[key + "_json"] = sa.cast(sa.literal(json.dumps(preset[key], ensure_ascii=False)), postgresql.JSONB())
        values["review_source_json"] = sa.cast(sa.literal("{}"), postgresql.JSONB())
        originals = (
            sa.select(sa.func.min(actions.c.id))
            .where(
                actions.c.source == "preset",
                actions.c.name == preset["name"],
                actions.c.preset_key.is_(None),
            )
            .group_by(actions.c.user_id)
        )
        op.execute(actions.update().where(actions.c.id.in_(originals)).values(**values, updated_at=sa.func.now()))
        columns = ["user_id", *values]
        literals = [
            sa.literal(value) if not isinstance(value, sa.sql.ClauseElement) else value for value in values.values()
        ]
        op.execute(
            actions.insert().from_select(
                columns,
                sa.select(users.c.id, *literals).where(
                    ~sa.exists(
                        sa.select(actions.c.id).where(
                            actions.c.user_id == users.c.id, actions.c.preset_key == preset["key"]
                        )
                    )
                ),
            )
        )
    op.execute(
        actions.update()
        .where(
            actions.c.status == "deferred",
            actions.c.review_reason.in_(
                (
                    "评审未完成，请显式重试",
                    "评审中断，请手动重试",
                    "动作评审未完成，需检查失败原因后手动重试",
                    "动作评审因程序错误中断，未取得评审结论；不代表动作内容被拒绝。",
                )
            ),
        )
        .values(status="failed")
    )


def downgrade() -> None:
    op.execute("UPDATE action_definitions SET status = 'deferred' WHERE status = 'failed'")
    op.drop_constraint("uq_action_definitions_preset", "action_definitions", type_="unique")
    op.drop_column("action_definitions", "preset_key")
