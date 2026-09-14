import json
from typing import Literal

from components import session_scope, tool_error
from modules.companion import CompanionWaitRequest, CompanionWakeEvent

from services.domains.companion import cancel_companion_wait, list_companion_intents, set_companion_wait
from services.domains.conversation import resolve_memory_scope
from services.infrastructure.tool_runtime import ToolsRegistry

COMPANION_WAIT_SCHEMA: dict[str, object] = {
    "name": "companion_wait",
    "description": (
        "Save a one-time companion follow-up for a later time or meaningful desktop event, or inspect, update "
        "and cancel existing intentions. Use cronjob for recurring fixed schedules. schedule creates an intention "
        "or replaces the specified one; list also returns failed runs whose tool effects may need verification. "
        "In a proactive turn, only the current intention can be scheduled or cancelled, and changes take effect "
        "only if the turn finishes successfully. Save future work here instead of polling or waiting with tools. "
        "At wake-up the desktop must be online, available, and outside still mode; expiry can end the wait "
        "without contact. Do not promise an exact delivery time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["schedule", "list", "cancel"]},
            "intent_id": {
                "type": "integer",
                "description": "Existing intent to update or cancel. Omit to create; proactive turns defer their current intent.",
            },
            "intent": {
                "type": "string",
                "description": "Grounded purpose, verified progress and what remains to check. A plan is not a completed action or new authorization.",
            },
            "after_seconds": {
                "type": "integer",
                "minimum": 60,
                "maximum": 2592000,
                "description": "Time-based wake-up delay. If wake_on is also set, either condition may wake the intent.",
            },
            "wake_on": {
                "type": "string",
                "enum": ["desktop_available", "context_changed"],
                "description": "Wait for desktop availability to resume, or a change of application category/fullscreen state. The event does not itself prove the user is free.",
            },
            "expires_seconds": {
                "type": "integer",
                "minimum": 120,
                "maximum": 2592000,
                "description": "Validity window, default one day, later than after_seconds. Deferral cannot extend the original intent's expiry.",
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
