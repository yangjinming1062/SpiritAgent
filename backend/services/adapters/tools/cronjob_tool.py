import json
from typing import Any

from components import coerce_int, get_logger, session_scope, tool_error
from prompts.tools import CRONJOB_DESC, CRONJOB_PARAM_DESCS

from services.contracts import MemoryScope
from services.domains.automation import create_job, get_job, list_jobs, remove_job, update_job
from services.domains.conversation import resolve_memory_scope
from services.infrastructure.tool_runtime import ToolsRegistry

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


_UPDATE_VERBS = {"update": "updated", "pause": "paused", "resume": "resumed"}


async def _handle_cron_action(
    action: str,
    scope: MemoryScope,
    job_id_raw: int | str | None,
    prompt: str | None,
    schedule: str | None,
    name: str | None,
    kind: str | None,
) -> str:
    if action == "create":
        if not schedule or not prompt:
            return tool_error("schedule and prompt are required for create")
        try:
            job = await create_job(
                scope=scope,
                prompt=prompt,
                schedule=schedule,
                name=name or "cron job",
                kind=kind or "standard",
            )
        except ValueError as e:
            return tool_error(str(e))
        return json.dumps(
            {"success": True, "message": f"Cron job '{job.get('name')}' created.", "job": job},
            ensure_ascii=False,
        )
    if action == "list":
        return json.dumps({"success": True, "jobs": await list_jobs(scope=scope)}, ensure_ascii=False)
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
    except Exception as e:
        logger.exception("cronjob tool error")
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
                "description": CRONJOB_PARAM_DESCS["schedule"],
            },
            "name": {"type": "string", "description": CRONJOB_PARAM_DESCS["name"]},
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
