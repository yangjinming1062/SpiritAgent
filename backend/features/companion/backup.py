"""模块拥有的备份表、校验与恢复操作。"""

from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from infrastructure.text import utc_now

from .intent_contracts import CompanionIntentView
from .intent_models import CompanionIntent
from .models import Persona


def _prepare_intent(raw: dict[str, Any], payload: dict[str, Any], _context: BackupContext) -> None:
    CompanionIntentView.model_validate({**payload, "id": raw["id"]}, strict=True)
    if payload["status"] == "queued":
        payload["status"] = "waiting"
    elif payload["status"] == "running":
        payload["status"] = "failed"
        payload["last_error"] = "Restored interrupted run; verify previous tool effects before rescheduling."
        payload["updated_at"] = utc_now()
    payload["lease_token"] = None
    payload["lease_until"] = None
    payload["event_received_at"] = None


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable("personas", Persona, unique_keys=()),
        BackupTable("companion_intents", CompanionIntent, prepare_payload=_prepare_intent, strict_references=True),
    )
