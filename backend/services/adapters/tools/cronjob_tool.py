import json
from typing import Any

from components import coerce_int, get_logger, tool_error

from services.domains.automation.cron_jobs import (
    create_job,
    get_job,
    list_jobs,
    pause_job,
    remove_job,
    resume_job,
    update_job,
)
from services.infrastructure.tool_runtime import REGISTRY

logger = get_logger(__name__)


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


async def _handle_cron_action(
    action: str,
    user_id: int,
    job_id_raw: int | str | None,
    prompt: str | None,
    schedule: str | None,
    name: str | None,
    deliver: str,
    kind: str | None,
) -> str:
    match action:
        case "create":
            if not schedule or not prompt:
                return tool_error("schedule and prompt are required for create")
            try:
                job = await create_job(
                    user_id=user_id,
                    prompt=prompt,
                    schedule=schedule,
                    name=name or "cron job",
                    deliver=deliver,
                    kind=kind or "standard",
                )
            except ValueError as e:
                return tool_error(str(e))
            return json.dumps(
                {"success": True, "message": f"Cron job '{job.get('name')}' created.", "job": job},
                ensure_ascii=False,
            )
        case "list":
            jobs = await list_jobs(user_id=user_id)
            return json.dumps({"success": True, "jobs": jobs}, ensure_ascii=False)
        case "update":
            job_id = coerce_int(job_id_raw, None)
            if job_id is None:
                return tool_error("job_id is required for update")
            updates = _build_updates(prompt, name, schedule, kind)
            job = await update_job(user_id=user_id, job_id=job_id, updates=updates)
            if not job:
                return tool_error(f"Cron job #{job_id_raw} not found.")
            return json.dumps(
                {"success": True, "message": f"Cron job #{job['id']} updated.", "job": job},
                ensure_ascii=False,
            )
        case "remove":
            job_id = coerce_int(job_id_raw, None)
            if job_id is None:
                return tool_error("job_id is required for remove")
            ok = await remove_job(user_id=user_id, job_id=job_id)
            if not ok:
                return tool_error(f"Cron job #{job_id_raw} not found.")
            return json.dumps({"success": True, "message": f"Cron job #{job_id_raw} removed."}, ensure_ascii=False)
        case "pause":
            job_id = coerce_int(job_id_raw, None)
            if job_id is None:
                return tool_error("job_id is required for pause")
            job = await pause_job(user_id=user_id, job_id=job_id)
            if not job:
                return tool_error(f"Cron job #{job_id_raw} not found.")
            return json.dumps(
                {"success": True, "message": f"Cron job #{job['id']} paused.", "job": job},
                ensure_ascii=False,
            )
        case "resume":
            job_id = coerce_int(job_id_raw, None)
            if job_id is None:
                return tool_error("job_id is required for resume")
            job = await resume_job(user_id=user_id, job_id=job_id)
            if not job:
                return tool_error(f"Cron job #{job_id_raw} not found.")
            return json.dumps(
                {"success": True, "message": f"Cron job #{job['id']} resumed.", "job": job},
                ensure_ascii=False,
            )
        case "get":
            job_id = coerce_int(job_id_raw, None)
            if job_id is None:
                return tool_error("job_id is required for get")
            job = await get_job(user_id=user_id, job_id=job_id)
            if not job:
                return tool_error(f"Cron job #{job_id_raw} not found")
            return json.dumps({"success": True, "job": job}, ensure_ascii=False)
        case _:
            return tool_error(
                f"Unknown cronjob action: {action!r}. Allowed: create, list, update, remove, pause, resume, get.",
            )


async def cronjob(
    action: str,
    user_id: int,
    job_id: int | None = None,
    prompt: str | None = None,
    schedule: str | None = None,
    name: str | None = None,
    deliver: str = "local",
    kind: str | None = None,
    **_,
) -> str:
    normalized = (action or "").strip().lower()
    try:
        return await _handle_cron_action(normalized, user_id, job_id, prompt, schedule, name, deliver, kind)
    except Exception as e:
        logger.exception("cronjob tool error")
        return tool_error(str(e))


CRONJOB_SCHEMA = {
    "name": "cronjob",
    "description": "Manage the user's scheduled cron jobs.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "One of: create, list, update, get, pause, resume, remove."},
            "job_id": {"type": "integer", "description": "Required for update/pause/resume/remove."},
            "prompt": {"type": "string", "description": "For create: the full prompt/instructions for the job."},
            "schedule": {
                "type": "string",
                "description": "For create/update: cron expression (e.g., '0 9 * * *' for daily at 9am).",
            },
            "name": {"type": "string", "description": "Optional human-friendly name."},
            "kind": {
                "type": "string",
                "enum": ["special", "standard"],
                "description": "special writes a natural proactive message to the companion conversation; standard runs in a separate task conversation and posts a system notification. Defaults to standard.",
                "default": "standard",
            },
            "deliver": {
                "type": "string",
                "description": "Delivery channel for job output (e.g., 'local', 'webhook'). Defaults to 'local' when omitted.",
                "default": "local",
            },
        },
        "required": ["action"],
    },
}


def register(registry) -> None:
    REGISTRY.register("cronjob", CRONJOB_SCHEMA, cronjob)
