"""共享任务行和请求身份的新格式备份贡献。"""

from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from infrastructure.job_models import JobRecord

from .models import JobAsset, JobRequest


def _prepare_job(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if payload["state"] in {"queued", "running", "paused", "unknown"}:
        payload["state"] = "unknown"
        payload["error"] = "导入的制作记录需要先核对原执行结果，不会自动重新提交"
        payload["finished_at"] = None
    payload["cancel_requested"] = False


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable("jobs", JobRecord, unique_keys=("source_key",), prepare_payload=_prepare_job),
        BackupTable("job_requests", JobRequest, unique_keys=("request_id",)),
        BackupTable("job_assets", JobAsset, unique_keys=("job_id", "asset_id", "role")),
    )
