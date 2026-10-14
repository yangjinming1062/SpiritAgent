from typing import Any

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.execution.api import get_process_registry

PROCESS_SCHEMA = {
    "name": "process",
    "description": (
        "Manage processes started with terminal(background=true). action='list' takes no session_id; every other "
        "action uses terminal's session_id. poll checks status, log reads output, wait blocks, kill terminates, "
        "write sends stdin, submit sends stdin plus Enter, and close sends EOF. write/submit/close change state and "
        "stdin works only for local processes. After a timeout or unknown result, poll before repeating a state change."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "poll", "log", "wait", "kill", "write", "submit", "close"],
                "description": "Action to perform on background processes",
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Process session ID from terminal(background=true). Required for every action except 'list'; "
                    "do not invent or reuse an ID from another task."
                ),
            },
            "data": {
                "type": "string",
                "description": "Required for 'write' and 'submit'. Text sent to stdin; 'submit' appends Enter.",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to block for 'wait' action. Returns partial output on timeout.",
                "minimum": 1,
            },
            "offset": {
                "type": "integer",
                "description": "Line offset for 'log' action (default: last 200 lines)",
                "minimum": 0,
            },
            "limit": {"type": "integer", "description": "Max lines to return for 'log' action", "minimum": 1},
        },
        "required": ["action"],
        "allOf": [
            {
                "if": {"properties": {"action": {"const": "list"}}},
                "then": {"not": {"required": ["session_id"]}},
            },
            {
                "if": {
                    "properties": {
                        "action": {
                            "enum": ["poll", "log", "wait", "kill", "write", "submit", "close"],
                        },
                    },
                },
                "then": {"required": ["session_id"]},
            },
            {
                "if": {"properties": {"action": {"enum": ["write", "submit"]}}},
                "then": {"required": ["data"]},
            },
        ],
    },
}


def _coerce_int(value: Any, field_name: str) -> int | None:
    """模型可能把整数参数发成字符串；无法转换时抛 ValueError，由调用方转成错误信封。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer, got {type(value).__name__}: {value!r}") from None


def _handle_process(args: dict[str, Any], **kw: Any) -> dict[str, Any]:
    process_registry = get_process_registry()
    action = args.get("action", "")
    valid_actions = {"list", "poll", "log", "wait", "kill", "write", "submit", "close"}
    if not isinstance(action, str) or action not in valid_actions:
        return {"error": f"Unknown process action: {action}. Use: list, poll, log, wait, kill, write, submit, close"}
    # 兼容模型把 session_id 发成整数，但拒绝把任意对象转成看似有效的 ID。
    raw_session_id = args.get("session_id")
    if raw_session_id is not None and (isinstance(raw_session_id, bool) or not isinstance(raw_session_id, str | int)):
        return {"error": "session_id must be a string"}
    if action == "list" and "session_id" in args:
        return {"error": "session_id must be omitted for list"}
    session_id = str(raw_session_id) if raw_session_id is not None else ""
    if action != "list" and not session_id:
        return {"error": f"session_id is required for {action}"}
    if action in {"write", "submit"}:
        if "data" not in args:
            return {"error": f"data is required for {action}"}
        if not isinstance(args["data"], str):
            return {"error": f"data must be a string for {action}"}
    try:
        offset = _coerce_int(args.get("offset"), "offset")
        limit = _coerce_int(args.get("limit"), "limit")
        timeout = _coerce_int(args.get("timeout"), "timeout")
        for field_name, value in (("limit", limit), ("timeout", timeout)):
            if value is not None and value < 1:
                raise ValueError(f"{field_name} must be at least 1")
        if offset is not None and offset < 0:
            raise ValueError("offset must be at least 0")
    except ValueError as e:
        return {"error": str(e)}
    match action:
        case "list":
            return {"processes": process_registry.list_sessions(task_id=kw.get("task_id"))}
        case "poll":
            return process_registry.poll(session_id)
        case "log":
            return process_registry.read_log(
                session_id,
                offset=0 if offset is None else offset,
                limit=200 if limit is None else limit,
            )
        case "wait":
            return process_registry.wait(session_id, timeout=timeout)
        case "kill":
            return process_registry.kill_process(session_id)
        case "write":
            return process_registry.write_stdin(session_id, str(args.get("data", "")))
        case "submit":
            return process_registry.submit_stdin(session_id, str(args.get("data", "")))
        case "close":
            return process_registry.close_stdin(session_id)
        case _:
            # action was validated above; keep a defensive branch if the dispatch table changes.
            return {"error": f"Unknown process action: {action}"}


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="process",
            toolset_id="process_management",
            schema=PROCESS_SCHEMA,
            handler=lambda context, args: _handle_process(
                args,
                task_id=context.task_id,
                cancel_token=context.cancel_event,
            ),
            check_fn=None,
            is_async=False,
        ),
    ]
