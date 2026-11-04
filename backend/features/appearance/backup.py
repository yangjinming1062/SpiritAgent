"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.backup import BackupContext, BackupTable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.jobs.service import rewrite_job_checkpoints
from features.media.identity_contracts import VisualIdentitySnapshot, VisualObservation

from .models import (
    AppearanceAsset,
    AppearancePreparationJob,
    AvatarAsset,
    CompanionOutfit,
    FullbodyCandidate,
    FullbodyReviewJob,
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


async def _restore_outfit_reviews(
    db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext
) -> None:
    ids = [
        int(context.id_map["outfit_creation_jobs"][str(raw["id"])])
        for raw in rows.get("outfit_creation_jobs", [])
        if ("outfit_creation_jobs", str(raw["id"])) in context.rewriter.inserted_rows
    ]

    def rewrite(checkpoint: dict | None) -> dict | None:
        if checkpoint is None:
            return None
        value = dict(checkpoint)
        if value.get("identity"):
            identity = VisualIdentitySnapshot.model_validate(value["identity"])
            mapped = context.id_map.get("avatar_assets", {}).get(str(identity.avatar_id))
            value["identity"] = identity.model_copy(
                update={"avatar_id": int(mapped) if mapped is not None else None}
            ).model_dump(mode="json")
        manual = value.get("manual_input")
        if isinstance(manual, dict) and manual.get("identity"):
            identity = VisualIdentitySnapshot.model_validate(manual["identity"])
            mapped = context.id_map.get("avatar_assets", {}).get(str(identity.avatar_id))
            value["manual_input"] = {
                **manual,
                "identity": identity.model_copy(
                    update={"avatar_id": int(mapped) if mapped is not None else None}
                ).model_dump(mode="json"),
            }
        return value

    await rewrite_job_checkpoints(db, context.user_id, ids, rewrite)


async def _restore_review_links(
    db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext
) -> None:
    for model, field in ((AvatarAsset, "fullbody_review_json"), (FullbodyCandidate, "review_json")):
        table = model.__tablename__
        ids = [
            int(context.id_map[table][str(raw["id"])])
            for raw in rows.get(table, [])
            if (table, str(raw["id"])) in context.rewriter.inserted_rows
        ]
        for row in await db.scalars(select(model).where(model.user_id == context.user_id, model.id.in_(ids))):
            value = getattr(row, field)
            if value and value.get("job_id"):
                mapped = context.id_map.get("jobs", {}).get(str(value["job_id"]))
                setattr(row, field, {**value, "job_id": int(mapped) if mapped is not None else None})


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "outfit_creation_jobs",
            OutfitCreationJob,
            foreign_keys={"id": "jobs", "result_outfit_id": "companion_outfits"},
            strict_references=True,
            restore_context=_restore_outfit_reviews,
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
            restore_context=_restore_review_links,
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
            "fullbody_review_jobs",
            FullbodyReviewJob,
            foreign_keys={"id": "jobs", "candidate_id": "companion_fullbody_candidates"},
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
