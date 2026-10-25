"""环境素材与制作详情的新格式备份贡献。"""

from typing import Any

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.backup import BackupContext, BackupTable

from .models import CompanionScene, SceneJob
from .schemas import SceneDescriptionRequest, SceneImageDimensions, SceneImageSize


def _prepare(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if payload.get("status") == "pending":
        payload["status"] = "failed"
    if payload.get("generation_origin_json"):
        GenerationOrigin.model_validate(payload["generation_origin_json"])
    if payload.get("status") == "ready":
        SceneDescriptionRequest.model_validate(
            {"title": payload.get("title"), "description": payload.get("description")}
        )
        if not payload.get("media_path"):
            raise ValueError("Ready scene image is missing from backup")
    for field, schema in (
        ("target_size_json", SceneImageSize),
        ("source_size_json", SceneImageDimensions),
        ("image_size_json", SceneImageDimensions),
    ):
        if payload.get(field):
            schema.model_validate(payload[field])


def _prepare_job(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if payload.get("availability") == "pending":
        payload["availability"] = "failed"


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable("companion_scenes", CompanionScene, unique_keys=("source_key",), prepare_payload=_prepare),
        BackupTable("scene_jobs", SceneJob, prepare_payload=_prepare_job),
    )
