"""一次性数据升级检查；不解析当前凭据，不联系供应商。"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class UntrackedVideo:
    kind: str
    entity_id: int
    generation_id: str | None
    state_sha256: str


def video_upgrade_state_sha256(row: Mapping[str, object]) -> str:
    state = {
        key: row[key]
        for key in ("generation_state_json", "status", "provider_task_id", "media_type", "artifact_path")
        if key in row
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def untracked_video_attempts(connection: Connection) -> list[UntrackedVideo]:
    unresolved: list[UntrackedVideo] = []
    terminal = {"submit_failed", "provider_failed", "content_policy_blocked", "queue_stopped"}
    for kind, table in (
        ("media", "video_gen_jobs"),
        ("action", "companion_actions"),
        ("desktop", "desktop_video_actions"),
    ):
        rows = connection.execute(text(f"SELECT * FROM {table} WHERE generation_state_json IS NOT NULL")).mappings()
        for row in rows:
            progress = json.loads(row["generation_state_json"] or "{}")
            state = (
                json.loads(progress["video_chain_json"])
                if kind == "desktop" and progress.get("video_chain_json")
                else progress
            )
            if kind == "desktop" and not progress.get("video_chain_json"):
                continue
            if state.get("queue_request_id") or state.get("result_url") or state.get("source_path"):
                continue
            if state.get("stop_reason") in terminal:
                continue
            if kind == "media":
                pending = row["status"] in {"submitting", "processing", "result_unknown"} or bool(
                    state.get("phase") == "submitting" or row["provider_task_id"],
                )
            elif kind == "action":
                pending = (
                    row["media_type"] == "video"
                    and not row["artifact_path"]
                    and bool(
                        state.get("phase") == "submitting" or row["provider_task_id"],
                    )
                )
            else:
                pending = (
                    not progress.get("download_url")
                    and not progress.get("source_path")
                    and bool(
                        state.get("phase") == "submitting"
                        or progress.get("provider_task_id")
                        or progress.get("submission_unknown"),
                    )
                )
            if pending:
                generation = state.get("generation_id")
                unresolved.append(
                    UntrackedVideo(
                        kind,
                        row["id"],
                        generation if isinstance(generation, str) and generation else None,
                        video_upgrade_state_sha256(row),
                    ),
                )
    return unresolved


def require_video_queue_upgrade_ready(connection: Connection) -> None:
    pending = untracked_video_attempts(connection)
    if pending:
        identifiers = ", ".join(f"{item.kind}:{item.entity_id}" for item in pending)
        raise RuntimeError(
            f"存在缺少原密钥归属的未决视频任务：{identifiers}。停止旧执行器后运行 "
            "python -m tools.video_queue_upgrade 检查；查实远端终态后再迁移，不能按当前凭据补录。",
        )
