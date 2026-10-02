import json
from typing import Any

from components import coerce_int, session_scope, tool_error, utc_now
from prompts.tools import CRONJOB_DESC, CRONJOB_PARAM_DESCS

from services.contracts import MemoryScope
from services.domains.automation import compute_next_run_at, create_job, get_job, list_jobs, remove_job, update_job
from services.domains.conversation import resolve_memory_scope
from services.infrastructure.tool_runtime import ToolsRegistry


def _build_updates(prompt: str | None, name: str | None, schedule: str | None, kind: str | None) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if prompt is not None:
        updates["prompt"] = prompt
    if name is not None:
        updates["name"] = name
    if schedule is not None:
        updates["schedule"] = schedule
        # 新 schedule 隐含取消暂停。
        updates["is_paused"] = False
    if kind is not None:
        updates["kind"] = kind
    return updates


_UPDATE_VERBS = {"update": "updated", "pause": "paused", "resume": "resumed"}
# 无法解析的表达式会被保存为暂停状态；在工具入口拒绝，避免模型误以为任务已安排。
_INVALID_SCHEDULE = "Invalid schedule {schedule!r}: use a five-field UTC cron expression such as '0 9 * * *'."


async def _handle_cron_action(
    action: str,
    scope: MemoryScope,
    job_id_raw: int | str | None,
    prompt: str | None,
    schedule: str | None,
    name: str | None,
    kind: str | None,
) -> str:
    if schedule and compute_next_run_at(schedule, utc_now()) is None:
        return tool_error(_INVALID_SCHEDULE.format(schedule=schedule))
    if action == "create":
        if not schedule or not prompt:
            return tool_error("schedule and prompt are required for create")
        job = await create_job(
            scope=scope,
            prompt=prompt,
            schedule=schedule,
            name=name or "cron job",
            kind=kind or "standard",
        )
        return json.dumps(
            {"success": True, "message": f"Cron job '{job.get('name')}' created.", "job": job},
            ensure_ascii=False,
        )
    if action == "list":
        return json.dumps(
            {"success": True, "jobs": await list_jobs(scope=scope, include_paused=True)},
            ensure_ascii=False,
        )
    if action not in {"remove", "get", *_UPDATE_VERBS}:
        return tool_error(
            f"Unknown cronjob action: {action!r}. Allowed: create, list, update, remove, pause, resume, get.",
        )

    job_id = coerce_int(job_id_raw, None)
    if job_id is None:
        return tool_error(f"job_id is required for {action}")
    if action == "remove":
        if not await remove_job(scope=scope, job_id=job_id):
            return tool_error(f"Cron job #{job_id_raw} not found.")
        return json.dumps({"success": True, "message": f"Cron job #{job_id_raw} removed."}, ensure_ascii=False)
    if action == "get":
        job = await get_job(scope=scope, job_id=job_id)
        if not job:
            return tool_error(f"Cron job #{job_id_raw} not found")
        return json.dumps({"success": True, "job": job}, ensure_ascii=False)

    updates = _build_updates(prompt, name, schedule, kind) if action == "update" else {"is_paused": action == "pause"}
    job = await update_job(scope=scope, job_id=job_id, updates=updates)
    if not job:
        return tool_error(f"Cron job #{job_id_raw} not found.")
    return json.dumps(
        {"success": True, "message": f"Cron job #{job['id']} {_UPDATE_VERBS[action]}.", "job": job},
        ensure_ascii=False,
    )


async def cronjob(
    action: str,
    user_id: int,
    parent_session_id: str,
    job_id: int | None = None,
    prompt: str | None = None,
    schedule: str | None = None,
    name: str | None = None,
    kind: str | None = None,
    **_: object,
) -> str:
    normalized = (action or "").strip().lower()
    try:
        async with session_scope() as db:
            scope = await resolve_memory_scope(db, user_id, parent_session_id)
        return await _handle_cron_action(normalized, scope, job_id, prompt, schedule, name, kind)
    except ValueError as e:
        # 参数与作用域校验的提示可直接回给模型；其他异常交给注册表脱敏并记录，不把 SQL 与参数暴露为工具结果。
        return tool_error(str(e))


CRONJOB_SCHEMA = {
    "name": "cronjob",
    "description": CRONJOB_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": CRONJOB_PARAM_DESCS["action"]},
            "job_id": {"type": "integer", "description": CRONJOB_PARAM_DESCS["job_id"]},
            "prompt": {"type": "string", "description": CRONJOB_PARAM_DESCS["prompt"]},
            "schedule": {
                "type": "string",
                "maxLength": 128,
                "description": CRONJOB_PARAM_DESCS["schedule"],
            },
            "name": {"type": "string", "maxLength": 128, "description": CRONJOB_PARAM_DESCS["name"]},
            "kind": {
                "type": "string",
                "enum": ["special", "standard"],
                "description": CRONJOB_PARAM_DESCS["kind"],
                "default": "standard",
            },
        },
        "required": ["action"],
    },
}


def register(registry: ToolsRegistry) -> None:
    registry.register(CRONJOB_SCHEMA, cronjob)
