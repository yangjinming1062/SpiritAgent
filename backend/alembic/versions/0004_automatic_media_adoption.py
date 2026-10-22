"""生成素材由服务端检查后自动采用。"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _passed(score: Any) -> bool:
    return type(score) is int and score >= 75


def upgrade() -> None:
    connection = op.get_bind()
    # 窗口动作的最终逐帧结论未持久保存，旧人工候选不能仅凭身份分放行。
    connection.execute(
        sa.text("""
        UPDATE companion_actions SET status='failed', stage='quality_failed',
        error='该候选缺少完整检查记录，请重新生成'
        WHERE status='review'
    """),
    )
    connection.execute(
        sa.text("""
        UPDATE companion_action_packs SET status='failed',
        error=COALESCE(NULLIF(identity_review_reason, ''), '动作组未通过自动检查，请重新制作')
        WHERE identity_review='review' AND NOT active
    """),
    )
    rows = (
        connection.execute(
            sa.text("""
        SELECT id, status, stage, generation_state_json FROM desktop_video_actions
        WHERE generation_state_json IS NOT NULL
    """),
        )
        .mappings()
        .all()
    )
    for row in rows:
        progress = _load(row["generation_state_json"])
        progress.pop("force_review", None)
        progress.pop("auto_continue", None)
        status, stage, error = row["status"], row["stage"], None
        if status == "review_pending":
            complete = (
                progress.get("candidate")
                and progress.get("review_verdict") == "pass"
                and _passed(progress.get("identity_score"))
                and not progress.get("submission_unknown")
            )
            if complete:
                # 生命周期只做本地发布，仍由所有者核对来源、基准和分辨率。
                status, stage = "processing", "review"
                progress["reprocess_only"] = False
            else:
                opening = progress.get("opening_review") or {}
                image_ready = (
                    opening.get("status", "complete") == "complete"
                    and opening.get("verdict") == "pass"
                    and _passed(opening.get("score"))
                )
                status, stage = "failed", "paused" if image_ready else "checking"
                progress["paused_by_user"] = True
                progress["image_confirmed"] = bool(image_ready)
                progress["failure_reason"] = "" if image_ready else "quality_failed"
                error = "原画面已保存，请继续制作" if image_ready else "该候选缺少完整合格检查，请重新生成"
        connection.execute(
            sa.text("""
            UPDATE desktop_video_actions SET generation_state_json=:progress,
            status=:status, stage=:stage, error=CASE WHEN :changed THEN :error ELSE error END
            WHERE id=:id
        """),
            {
                "id": row["id"],
                "progress": _dump(progress),
                "status": status,
                "stage": stage,
                "error": error,
                "changed": row["status"] == "review_pending",
            },
        )
    rows = (
        connection.execute(
            sa.text("""
        SELECT id, video_url, candidate_video_url, generation_state_json
        FROM video_gen_jobs WHERE status='review_pending'
    """),
        )
        .mappings()
        .all()
    )
    for row in rows:
        state = _load(row["generation_state_json"])
        path = row["video_url"] or row["candidate_video_url"]
        passed = any(
            candidate.get("path") == path and candidate.get("evaluated") and _passed(candidate.get("score"))
            for candidate in state.get("candidates", [])
        )
        connection.execute(
            sa.text("""
            UPDATE video_gen_jobs SET status=:status, video_url=:path,
            error_reason=:reason, error_message=:error WHERE id=:id
        """),
            {
                "id": row["id"],
                "path": path,
                "status": "succeeded" if passed else "failed",
                "reason": None if passed else "quality_failed",
                "error": None if passed else "原视频未通过完整检查，内容未发布",
            },
        )
    connection.execute(
        sa.text("""
        UPDATE post_publications SET status=CASE
            WHEN video_job_id IN (SELECT id FROM video_gen_jobs WHERE status='succeeded')
            THEN 'result_unknown' ELSE 'failed' END,
        error='原视频已核对，等待完成发布或重新制作' WHERE status='review_pending'
    """),
    )
    # 释放人工记录独占引用；清理器仍会保护当前成品和有效制作进度。
    for row in connection.execute(sa.text("SELECT user_id, media_url FROM companion_media_reviews")).mappings().all():
        path = row["media_url"]
        if path and path.startswith(f"companion-assets/{row['user_id']}/"):
            connection.execute(
                sa.text("""
                INSERT INTO asset_cleanup_pending (user_id, path) VALUES (:user_id, :path)
                ON CONFLICT (user_id, path) DO NOTHING
            """),
                {"user_id": row["user_id"], "path": path},
            )
    op.drop_table("companion_media_reviews")


def downgrade() -> None:
    raise RuntimeError("自动采用无法恢复为原人工候选状态，回退请恢复升级前备份")
