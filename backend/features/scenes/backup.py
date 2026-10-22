"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.backup import BackupContext, BackupTable

from .models import CompanionScene
from .schemas import SceneDescriptionRequest, SceneImageDimensions, SceneImageSize


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:

    if table == "companion_scenes":
        if payload.get("generation_origin_json"):
            GenerationOrigin.model_validate_json(payload["generation_origin_json"])
        if payload.get("status") == "ready":
            SceneDescriptionRequest.model_validate(
                {"title": payload.get("title"), "description": payload.get("description")},
            )
            if not payload.get("media_path"):
                raise ValueError("Ready scene image is missing from backup")
        for field, schema in (
            ("target_size_json", SceneImageSize),
            ("source_size_json", SceneImageDimensions),
            ("image_size_json", SceneImageDimensions),
        ):
            if payload.get(field):
                schema.model_validate_json(payload[field])
        payload["auto_activate"] = False
        payload["regeneration_status"] = None
        payload["regeneration_stage"] = None
        payload["regeneration_error"] = None
        payload["regeneration_task_id"] = None
        if payload.get("status") == "pending":
            payload["status"] = "description_failed" if payload.get("media_path") else "failed"
            payload["error"] = "恢复的场景任务需要手动重试"


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "companion_scenes",
            CompanionScene,
            excluded_columns=frozenset({"generation_state_json", "regeneration_state_json", "reference_image"}),
            prepare_payload=partial(_prepare, "companion_scenes"),
        ),
    )
