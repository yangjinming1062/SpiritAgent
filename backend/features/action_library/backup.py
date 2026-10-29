"""动作素材的备份贡献：定义成组恢复，评审冻结依据随行保存。"""

from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ActionDefinition
from .presets import PRESETS_BY_KEY
from .schemas import ActionAssetDesign, ActionAssetSource, ActionAssetStatus, ActionReviewSource
from .views import normalize_action_name

_SOURCE = TypeAdapter(ActionAssetSource)
_STATUS = TypeAdapter(ActionAssetStatus)
_JSON_FIELDS = frozenset({"use_when", "avoid_when", "allowed_triggers", "semantic_tags"})


def _prepare(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    source = _SOURCE.validate_python(payload.get("source"))
    status = _STATUS.validate_python(payload.get("status"))
    if source == "preset":
        key = payload["preset_key"]
        if key not in PRESETS_BY_KEY:
            raise ValueError("备份包含未知的内置动作")
        preset = PRESETS_BY_KEY[key]
        for field in (
            "name",
            "motion_description",
            "clip_kind",
            "duration_seconds",
            "performance_role",
            "motion_intensity",
            "interruptible",
        ):
            payload[field] = getattr(preset, field)
        for field in ("use_when", "avoid_when", "allowed_triggers", "semantic_tags"):
            payload[field + "_json"] = getattr(preset, field)
        payload.update(
            preset_key=key,
            normalized_name=preset.name.casefold(),
            status="approved",
            reason="",
            review_reason="",
            review_source_json={},
        )
        return
    if payload.get("preset_key") is not None:
        raise ValueError("自创动作不能占用内置动作标识")
    definition = ActionAssetDesign.model_validate(
        {
            field: payload[field + "_json" if field in _JSON_FIELDS else field]
            for field in ActionAssetDesign.model_fields
        }
    )
    payload["normalized_name"] = normalize_action_name(definition.name)
    if status in {"pending", "reviewing"}:
        payload.update(status="failed", review_reason="评审中断，请手动重试")
    reference = payload.get("review_source_json")
    if reference != {} or payload["status"] != "failed":
        ActionReviewSource.model_validate(reference)


async def _find_existing(
    db: AsyncSession, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext
) -> int | None:
    key = payload.get("preset_key")
    if not key:
        return None
    return await db.scalar(
        select(ActionDefinition.id).where(
            ActionDefinition.user_id == context.user_id, ActionDefinition.preset_key == key
        )
    )


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            name="action_definitions",
            _model=ActionDefinition,
            foreign_keys={},
            unique_keys=None,
            strict_references=False,
            prepare_payload=_prepare,
            find_existing=_find_existing,
        ),
    )
