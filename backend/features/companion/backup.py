"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from infrastructure.text import utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
        BackupTable("personas", Persona, foreign_keys={"active_scene_id": "companion_scenes"}, unique_keys=()),
        BackupTable("companion_intents", CompanionIntent, prepare_payload=partial(_prepare, "companion_intents")),
    )


def cron_source_keys(job_ids: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(companion_cron_source_key(job_id) for job_id in job_ids)


async def scene_versions(db: AsyncSession, user_id: int) -> tuple[int, int] | None:
    row = (
        await db.execute(
            select(Persona.scene_state_version, Persona.scene_switch_version).where(Persona.user_id == user_id),
        )
    ).one_or_none()
    return tuple(row) if row is not None else None


async def advance_scene_versions(db: AsyncSession, user_id: int, previous: tuple[int, int] | None) -> None:
    persona = await db.scalar(
        select(Persona).where(Persona.user_id == user_id).execution_options(populate_existing=True),
    )
    if persona is None:
        return
    state, switch = previous or (0, 0)
    persona.scene_state_version = max(persona.scene_state_version, state) + 1
    persona.scene_switch_version = max(persona.scene_switch_version, switch) + 1
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.scene.updated",
        payload={"version": persona.scene_state_version, "switch_version": persona.scene_switch_version},
    )
