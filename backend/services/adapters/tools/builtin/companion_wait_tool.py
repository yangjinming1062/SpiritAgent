import json
from typing import Literal

from components import session_scope, tool_error
from modules.companion import CompanionWaitRequest, CompanionWakeEvent
from prompts.tools import COMPANION_WAIT_DESC, COMPANION_WAIT_PARAM_DESCS

from services.domains.companion import cancel_companion_wait, list_companion_intents, set_companion_wait
from services.domains.conversation import resolve_memory_scope
from services.infrastructure.tool_runtime import ToolsRegistry

COMPANION_WAIT_SCHEMA: dict[str, object] = {
    "name": "companion_wait",
    "description": COMPANION_WAIT_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["schedule", "list", "cancel"]},
            "intent_id": {
                "type": "integer",
                "description": COMPANION_WAIT_PARAM_DESCS["intent_id"],
            },
            "intent": {
                "type": "string",
                "description": COMPANION_WAIT_PARAM_DESCS["intent"],
            },
            "after_seconds": {
                "type": "integer",
                "minimum": 60,
                "maximum": 2592000,
                "description": COMPANION_WAIT_PARAM_DESCS["after_seconds"],
            },
            "wake_on": {
                "type": "string",
                "enum": ["desktop_available", "context_changed"],
                "description": COMPANION_WAIT_PARAM_DESCS["wake_on"],
            },
            "expires_seconds": {
                "type": "integer",
                "minimum": 120,
                "maximum": 2592000,
                "description": COMPANION_WAIT_PARAM_DESCS["expires_seconds"],
            },
        },
        "required": ["action"],
    },
}


async def companion_wait(
    action: Literal["schedule", "list", "cancel"],
    user_id: int,
    parent_session_id: str,
    intent: str | None = None,
    intent_id: int | None = None,
    after_seconds: int | None = None,
    wake_on: CompanionWakeEvent | None = None,
    expires_seconds: int = 86400,
    **kwargs: object,
) -> str:
    try:
        async with session_scope() as db:
            scope = await resolve_memory_scope(db, user_id, parent_session_id)
            if scope.system_preset_id != "companion":
                return tool_error("Companion waits are only available in the companion preset")
            if action == "list":
                rows = await list_companion_intents(db, user_id)
                return json.dumps({"intents": [row.model_dump(mode="json") for row in rows]}, ensure_ascii=False)
        if action == "cancel":
            if intent_id is None:
                raise ValueError("intent_id is required for cancellation")
            return json.dumps({"cancelled": await cancel_companion_wait(user_id, intent_id)})
        if action != "schedule":
            raise ValueError("action must be schedule, list or cancel")
        request = CompanionWaitRequest(
            intent=intent or "",
            after_seconds=after_seconds,
            wake_on=wake_on,
            expires_seconds=expires_seconds,
        )
        result = await set_companion_wait(user_id, request, intent_id)
        return json.dumps(
            {
                "intent_id": result,
                "scheduled": True,
                "note": "In a proactive turn this wait commits only when the turn finishes successfully.",
            },
        )
    except ValueError as exc:
        return tool_error(str(exc))


def register(registry: ToolsRegistry) -> None:
    registry.register("companion_wait", COMPANION_WAIT_SCHEMA, companion_wait)
