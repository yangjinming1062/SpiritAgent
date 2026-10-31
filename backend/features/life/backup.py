"""生活事实及显式任务、结果和证据关系的备份贡献。"""

from copy import deepcopy
from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from infrastructure.text import utc_now
from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.jobs.service import requested_job_id
from features.media.visual_semantics import SceneTags

from .actions import CREATION_CAPABILITIES, LIFE_ACTION, ReadingResult
from .models import LifeActionAdmission, LifeEvent, LifePursuit, LifeState, LifeStep, PursuitEvidence

_REFERENCES = {
    "job_id": "jobs",
    "visual_id": "visual_items",
    "visual_version_id": "visual_versions",
    "entry_id": "companion_entries",
    "outfit_id": "companion_outfits",
    "scene_id": "companion_scenes",
    "action_id": "action_definitions",
    "pursuit_id": "life_pursuits",
    "step_id": "life_steps",
    "reading_step_id": "life_steps",
    "event_id": "life_events",
    "cron_job_id": "cron_jobs",
}
_LIST_REFERENCES = {
    "event_ids": "life_events",
    "evidence_event_ids": "life_events",
    "step_ids": "life_steps",
    "topic_ids": "companion_topics",
}


async def _validate_admission(
    db: AsyncSession, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext
) -> None:
    if payload["capability"] not in CREATION_CAPABILITIES | {"content.read", "outreach.schedule"} or payload[
        "is_creation"
    ] != (payload["capability"] in CREATION_CAPABILITIES):
        raise ValueError("执行额度记录与能力性质不一致")
    existing = await db.scalar(
        select(LifeActionAdmission.capability).where(
            LifeActionAdmission.user_id == context.asset_owner_id,
            LifeActionAdmission.request_id == payload["request_id"],
        )
    )
    if existing is not None and existing != payload["capability"]:
        raise ValueError("备份中的执行请求与已有能力不一致")


async def _merge_admissions(
    db: AsyncSession, rows: list[dict[str, Any]], ids: dict[str, int | str], context: BackupContext
) -> None:
    submitted = [ids[str(raw["id"])] for raw in rows if raw["submitted"]]
    if submitted:
        await db.execute(
            update(LifeActionAdmission)
            .where(LifeActionAdmission.user_id == context.user_id, LifeActionAdmission.id.in_(submitted))
            .values(submitted=True)
        )


def _remap_id(value: Any, table: str, context: BackupContext) -> int | str | None:
    mapped = context.id_map.get(table, {}).get(str(value))
    if mapped is None and table in context.id_map:
        raise ValueError(f"Missing {table} reference in life data")
    return mapped


def _remap(value: Any, context: BackupContext) -> Any:
    if isinstance(value, list):
        return [_remap(item, context) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in _REFERENCES and item is not None:
            result[key] = _remap_id(item, _REFERENCES[key], context)
        elif key in _LIST_REFERENCES and isinstance(item, list):
            result[key] = [
                mapped
                for identifier in item
                if (mapped := _remap_id(identifier, _LIST_REFERENCES[key], context)) is not None
            ]
        else:
            result[key] = _remap(item, context)
    return result


def _prepare_step(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if payload["action_json"] is not None:
        action = LIFE_ACTION.validate_python(payload["action_json"])
        if payload["kind"] != action.capability:
            raise ValueError("生活步骤与动作能力不一致")
        payload["action_json"] = action.model_dump(mode="json")
        if payload["is_creation"] != (action.capability in CREATION_CAPABILITIES):
            raise ValueError("生活步骤与创作额度性质不一致")
    if payload["reading_result_json"] is not None:
        payload["reading_result_json"] = ReadingResult.model_validate(payload["reading_result_json"]).model_dump(
            mode="json"
        )
    if payload["state"] in {"running", "unknown"}:
        payload.update(
            state="unknown",
            disposition="unknown",
            wait_reason="恢复的执行结果须先核对原任务",
            finished_at=None,
            narrative=None,
        )


def _prepare_state(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    payload["scene_tags_json"] = TypeAdapter(SceneTags).validate_python(payload["scene_tags_json"])
    payload["presentation_evaluated_revision"] = 0
    payload["presentation_revision"] += 1
    payload["next_review_at"] = utc_now()


async def _restore_life(db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext) -> None:
    for table, model in (("life_pursuits", LifePursuit), ("life_steps", LifeStep), ("life_events", LifeEvent)):
        ids = [
            context.id_map[table][str(raw["id"])]
            for raw in rows.get(table, [])
            if (table, str(raw["id"])) in context.rewriter.inserted_rows
        ]
        if not ids:
            continue
        for row in await db.scalars(select(model).where(model.user_id == context.user_id, model.id.in_(ids))):
            row.result_ref_json = _remap(deepcopy(row.result_ref_json), context)
            if isinstance(row, LifeStep):
                admission = await db.scalar(
                    select(LifeActionAdmission).where(
                        LifeActionAdmission.user_id == context.user_id,
                        LifeActionAdmission.request_id == row.idempotency_key,
                    )
                )
                if admission is not None and admission.submitted and row.state in {"planned", "running", "unknown"}:
                    row.state, row.disposition, row.wait_reason = (
                        "unknown",
                        "unknown",
                        "这个请求已经提交过，须核对原结果",
                    )
                    row.failure_code = row.failure_code or "unknown_submission"
                    row.budget_date, row.started_at = admission.budget_date, admission.admitted_at
                    if row.job_id is None:
                        row.job_id = await requested_job_id(db, context.user_id, row.idempotency_key)
                if row.action_json is not None:
                    row.action_json = LIFE_ACTION.validate_python(_remap(row.action_json, context)).model_dump(
                        mode="json"
                    )
                row.checkpoint = _remap(row.checkpoint, context)
            elif isinstance(row, LifeEvent):
                row.fact_json = _remap(row.fact_json, context)


def backup_tables() -> tuple[BackupTable, ...]:
    result_keys = {
        "result_job_id": "jobs",
        "result_visual_id": "visual_items",
        "result_visual_version_id": "visual_versions",
        "result_entry_id": "companion_entries",
        "result_outfit_id": "companion_outfits",
        "result_scene_id": "companion_scenes",
        "result_action_id": "action_definitions",
    }
    return (
        BackupTable(
            "life_action_admissions",
            LifeActionAdmission,
            unique_keys=("request_id",),
            validate_payload=_validate_admission,
            after_rows=_merge_admissions,
        ),
        BackupTable(
            "life_pursuits",
            LifePursuit,
            foreign_keys={"previous_pursuit_id": "life_pursuits"},
            strict_references=True,
            restore_context=_restore_life,
        ),
        BackupTable(
            "life_steps",
            LifeStep,
            foreign_keys={
                "pursuit_id": "life_pursuits",
                "job_id": "jobs",
                "business_action_id": "action_definitions",
                **result_keys,
            },
            unique_keys=("idempotency_key",),
            strict_references=True,
            prepare_payload=_prepare_step,
        ),
        BackupTable(
            "life_events",
            LifeEvent,
            foreign_keys={"pursuit_id": "life_pursuits", "step_id": "life_steps", **result_keys},
            unique_keys=("event_key",),
            strict_references=True,
        ),
        BackupTable(
            "life_states",
            LifeState,
            unique_keys=(),
            foreign_keys={"active_pursuit_id": "life_pursuits", "activity_step_id": "life_steps"},
            strict_references=True,
            prepare_payload=_prepare_state,
        ),
        BackupTable(
            "pursuit_evidence",
            PursuitEvidence,
            foreign_keys={"pursuit_id": "life_pursuits", "event_id": "life_events"},
            unique_keys=("pursuit_id", "event_id"),
            strict_references=True,
        ),
    )
