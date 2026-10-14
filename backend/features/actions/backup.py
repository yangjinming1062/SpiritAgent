"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.backup import BackupContext, BackupTable

from .backup_assets import restore_action_payload
from .models import CompanionAction, CompanionActionPack

ACTION_TABLES = frozenset({"companion_action_packs", "companion_actions"})


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    user_id = context.user_id
    id_map = context.id_map
    if table in ACTION_TABLES:
        restore_action_payload(table, payload, id_map, user_id)


def _export_action(payload: dict[str, Any]) -> None:
    if payload.get("media_type") == "image":
        for name in ("kind", "target_duration_seconds", "actual_duration_ms", "frames", "loopable", "hitmask_fps"):
            payload.pop(name, None)


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "companion_action_packs",
            CompanionActionPack,
            foreign_keys={"avatar_id": "avatar_assets", "outfit_id": "companion_outfits"},
            strict_references=True,
            prepare_payload=partial(_prepare, "companion_action_packs"),
        ),
        BackupTable(
            "companion_actions",
            CompanionAction,
            foreign_keys={"pack_id": "companion_action_packs", "outfit_id": "companion_outfits"},
            strict_references=True,
            prepare_payload=partial(_prepare, "companion_actions"),
            export_payload=_export_action,
        ),
    )
