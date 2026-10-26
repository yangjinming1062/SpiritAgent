"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.backup import BackupContext, BackupTable

from features.media.identity_contracts import CharacterFeatures, CharacterOverrides

from .models import (
    AppearanceAsset,
    AvatarAsset,
    CharacterAnalysisJob,
    CompanionOutfit,
    FullbodyCandidate,
    OutfitCreationJob,
    OutfitSource,
)


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if table == "avatar_assets":
        for field in ("generation_origin_json", "fullbody_generation_origin_json"):
            if payload.get(field):
                payload[field] = GenerationOrigin.model_validate(payload[field]).model_dump(mode="json")
        revision = payload["revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("Invalid character card revision")
        payload["automatic_json"] = CharacterFeatures.model_validate(payload["automatic_json"]).model_dump(mode="json")
        payload["overrides_json"] = CharacterOverrides.model_validate(payload["overrides_json"]).model_dump(
            mode="json",
            exclude_none=True,
        )

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
                "analysis_job_id": "character_analysis_jobs",
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
            "character_analysis_jobs", CharacterAnalysisJob, foreign_keys={"id": "jobs", "avatar_id": "avatar_assets"}
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
                "analysis_job_id": "character_analysis_jobs",
                "asset_id": "assets",
            },
        ),
    )
