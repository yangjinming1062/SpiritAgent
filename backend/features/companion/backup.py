"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from infrastructure.text import utc_now

from .intent_contracts import CompanionIntentView
from .intent_models import COMPANION_CRON_SOURCE_PREFIX, CompanionIntent, companion_cron_source_key
from .models import Persona


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    id_map = context.id_map
    if table == "companion_intents":
        CompanionIntentView.model_validate({**payload, "id": raw["id"]}, strict=True)
        if payload["status"] == "queued":
            payload["status"] = "waiting"
        elif payload["status"] == "running":
            payload["status"] = "failed"
            payload["last_error"] = "Restored interrupted run; verify previous tool effects before rescheduling."
            payload["updated_at"] = utc_now()
        payload["lease_token"] = None
        payload["lease_until"] = None
        source_key = payload.get("source_key")
        if isinstance(source_key, str) and source_key.startswith(COMPANION_CRON_SOURCE_PREFIX):
            job_id = id_map.get("cron_jobs", {}).get(source_key.removeprefix(COMPANION_CRON_SOURCE_PREFIX))
            payload["source_key"] = companion_cron_source_key(int(job_id)) if job_id is not None else None
        payload["event_received_at"] = None


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable("personas", Persona, unique_keys=()),
        BackupTable("companion_intents", CompanionIntent, prepare_payload=partial(_prepare, "companion_intents")),
    )


def cron_source_keys(job_ids: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(companion_cron_source_key(job_id) for job_id in job_ids)
