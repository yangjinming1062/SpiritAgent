"""生活状态的备份贡献。"""

import json
from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeEvent, LifePursuit, LifeState, LifeStep

_RESULT_TABLES = {
    "visual_job_id": "visual_jobs",
    "visual_id": "visual_items",
    "post_id": "companion_posts",
    "diary_id": "companion_diary_entries",
    "pursuit_id": "life_pursuits",
    "step_id": "life_steps",
}


def _remap_results(value: Any, context: BackupContext) -> Any:
    if isinstance(value, list):
        return [_remap_results(item, context) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        table = _RESULT_TABLES.get(key)
        if table in context.id_map and item is not None:
            result[key] = context.id_map[table].get(str(item))
        elif key == "event_ids" and "life_events" in context.id_map and isinstance(item, list):
            result[key] = [
                mapped for event_id in item if (mapped := context.id_map["life_events"].get(str(event_id))) is not None
            ]
        else:
            result[key] = _remap_results(item, context)
    return result


def _prepare_results(_raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    for key in ("business_ref_json", "result_ref_json", "fact_json"):
        if payload.get(key):
            payload[key] = json.dumps(_remap_results(json.loads(payload[key]), context), ensure_ascii=False)


async def _link_result_events(
    db: AsyncSession,
    rows: dict[str, list[dict[str, Any]]],
    context: BackupContext,
) -> None:
    """事项的证据事件是前向引用，事件整表恢复后再映射。"""
    for table, model in (("life_pursuits", LifePursuit), ("life_events", LifeEvent)):
        for raw in rows.get(table, []):
            original_id = str(raw["id"])
            if (table, original_id) not in context.rewriter.inserted_rows:
                continue
            row = await db.get(model, context.id_map[table][original_id])
            if row is not None:
                row.result_ref_json = json.dumps(
                    _remap_results(json.loads(context.rewriter.rewrite(raw.get("result_ref_json") or "{}")), context),
                    ensure_ascii=False,
                )


async def _find_pursuit(
    db: AsyncSession,
    _raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> int | None:
    key = payload.get("idempotency_key")
    if not isinstance(key, str) or not key:
        return None
    return await db.scalar(
        select(LifePursuit.id).where(
            LifePursuit.user_id == context.user_id,
            LifePursuit.idempotency_key == key,
        ),
    )


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable("life_pursuits", LifePursuit, find_existing=_find_pursuit, prepare_payload=_prepare_results),
        BackupTable(
            "life_steps",
            LifeStep,
            foreign_keys={"pursuit_id": "life_pursuits"},
            unique_keys=("idempotency_key",),
            prepare_payload=_prepare_results,
        ),
        BackupTable(
            "life_events",
            LifeEvent,
            unique_keys=("event_key",),
            foreign_keys={"pursuit_id": "life_pursuits", "step_id": "life_steps"},
            prepare_payload=_prepare_results,
            restore_context=_link_result_events,
        ),
        BackupTable("life_states", LifeState, unique_keys=(), foreign_keys={"active_pursuit_id": "life_pursuits"}),
    )
