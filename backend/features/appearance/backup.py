"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.backup import BackupContext, BackupTable

from features.media.identity_contracts import CharacterFeatures, CharacterOverrides

from .character_card_models import CompanionCharacterCard
from .models import AvatarAsset, CompanionOutfit, OutfitSource


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:

    if table == "avatar_assets":
        for field in ("generation_origin_json", "fullbody_generation_origin_json"):
            if payload.get(field):
                payload[field] = GenerationOrigin.model_validate_json(payload[field]).model_dump_json()

    if table == "companion_character_cards":
        if payload.get("avatar_id") is None:
            raise ValueError("Character card avatar is missing from backup")
        revision = payload.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("Invalid character card revision")
        payload["automatic_json"] = CharacterFeatures.model_validate_json(payload["automatic_json"]).model_dump_json()
        payload["overrides_json"] = CharacterOverrides.model_validate_json(payload["overrides_json"]).model_dump_json(
            exclude_none=True,
        )
        payload["portrait_source_path"] = payload["body_source_path"] = ""
        payload["status"] = payload["portrait_status"] = payload["body_status"] = "ready" if revision else "failed"
        payload["error"] = None if revision else "恢复的角色资料尚未完成分析，请重试"

    if table == "companion_outfits":
        payload["source_json"] = OutfitSource.model_validate_json(payload["source_json"]).dump()
        if payload.get("status") == "ready":
            # 恢复后的首次准备由用户接管，不在恢复结束或重启时自动付费。
            payload["initial_video_started"] = True
        if payload.get("status") == "ready" and payload.get("description_status") in {"pending", "processing"}:
            payload["description_status"] = "failed"
            payload["description_error"] = "恢复的外观描述任务需要手动重试"


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable("avatar_assets", AvatarAsset, prepare_payload=partial(_prepare, "avatar_assets")),
        BackupTable(
            "companion_character_cards",
            CompanionCharacterCard,
            foreign_keys={"avatar_id": "avatar_assets"},
            unique_keys=("avatar_id",),
            excluded_columns=frozenset(
                {"portrait_result_json", "body_result_json", "portrait_source_path", "body_source_path"},
            ),
            prepare_payload=partial(_prepare, "companion_character_cards"),
        ),
        BackupTable("companion_outfits", CompanionOutfit, prepare_payload=partial(_prepare, "companion_outfits")),
    )
