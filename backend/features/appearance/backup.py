"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.backup import BackupContext, BackupTable

from features.media.identity_contracts import VisualObservation

from .models import (
    AppearanceAsset,
    AppearancePreparationJob,
    AvatarAsset,
    CompanionOutfit,
    FullbodyCandidate,
    OutfitCreationJob,
    OutfitSource,
    VisualObservationJob,
)


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if table == "avatar_assets":
        for field in ("generation_origin_json", "fullbody_generation_origin_json"):
            if payload.get(field):
                payload[field] = GenerationOrigin.model_validate(payload[field]).model_dump(mode="json")
        revision = payload["identity_revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("Invalid visual identity revision")
    if table == "visual_observation_jobs" and payload.get("result_json"):
        payload["result_json"] = VisualObservation.model_validate(payload["result_json"]).model_dump(mode="json")

    if table == "companion_outfits":
        payload["source_json"] = OutfitSource.model_validate(payload["source_json"]).dump()
        if payload.get("status") == "ready" and payload.get("description_status") in {"pending", "processing"}:
            payload["description_status"] = "failed"
            payload["description_error"] = "恢复的外观描述任务需要手动重试"


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "outfit_creation_jobs",
            OutfitCreationJob,
            foreign_keys={"id": "jobs", "result_outfit_id": "companion_outfits"},
            strict_references=True,
        ),
        BackupTable(
            "avatar_assets",
            AvatarAsset,
            foreign_keys={
                "preparation_job_id": "appearance_preparation_jobs",
                "portrait_asset_id": "assets",
                "fullbody_asset_id": "assets",
            },
            prepare_payload=partial(_prepare, "avatar_assets"),
        ),
        BackupTable(
            "companion_outfits",
            CompanionOutfit,
            foreign_keys={"asset_id": "assets"},
            prepare_payload=partial(_prepare, "companion_outfits"),
        ),
        BackupTable(
            "appearance_preparation_jobs",
            AppearancePreparationJob,
            foreign_keys={"id": "jobs", "avatar_id": "avatar_assets"},
        ),
        BackupTable(
            "visual_observation_jobs",
            VisualObservationJob,
            foreign_keys={"id": "jobs", "avatar_id": "avatar_assets"},
            prepare_payload=partial(_prepare, "visual_observation_jobs"),
        ),
        BackupTable(
            "companion_fullbody_candidates",
            FullbodyCandidate,
            foreign_keys={"avatar_id": "avatar_assets", "asset_id": "assets", "base_asset_id": "assets"},
        ),
        BackupTable(
            "appearance_assets",
            AppearanceAsset,
            foreign_keys={
                "avatar_id": "avatar_assets",
                "outfit_id": "companion_outfits",
                "candidate_id": "companion_fullbody_candidates",
                "preparation_job_id": "appearance_preparation_jobs",
                "observation_job_id": "visual_observation_jobs",
                "asset_id": "assets",
            },
        ),
    )
