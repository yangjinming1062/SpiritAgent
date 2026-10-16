from features.memory.profile_fields import MemoryProfileChange
from features.memory.use_cases import (
    forget_manual_memory,
    list_memory_view,
    resolve_scope,
    update_manual_memory,
    update_profile,
)
from infrastructure.constants import JSONRPC_INVALID_PARAMS, JSONRPC_METHOD_NOT_FOUND
from runtime.desktop.jsonrpc import JsonRpcError
from runtime.gateway_contracts import GatewaySession, RegisterMethod
from runtime.memory_scope import MemoryScope


def register_handlers(session: GatewaySession, register: RegisterMethod) -> None:
    user_id = session.user_id

    async def _memory_scope(params: dict) -> MemoryScope:
        try:
            if "session_id" in params:
                if "system_preset_id" in params or not isinstance(params["session_id"], str):
                    raise ValueError("Provide a session or a preset, not both")
                return await resolve_scope(user_id, params["session_id"], None)
            preset = params.get("system_preset_id")
            if not isinstance(preset, str):
                raise ValueError("session_id or system_preset_id is required")
            return await resolve_scope(user_id, None, preset)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc

    async def memory_list(params: dict) -> dict:
        kind = params.get("kind")
        tag = params.get("tag")
        q = params.get("q")
        limit = params.get("limit")
        offset = params.get("offset", 0)
        if type(offset) is not int or offset < 0:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "offset must be a non-negative int")
        try:
            scope = await _memory_scope(params)
            view = await list_memory_view(
                scope,
                kind=kind if isinstance(kind, str) else None,
                status=params.get("status", "active"),
                tag=tag if isinstance(tag, str) else None,
                query=q if isinstance(q, str) else None,
                limit=int(limit) if isinstance(limit, int) else 100,
                offset=offset,
            )
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))
        return {
            "memories": [row.model_dump() for row in view.memories],
            "counts": view.counts,
            "total": view.total,
            "system_preset_id": scope.system_preset_id,
            "session_id": params.get("session_id"),
        }

    async def memory_update(params: dict) -> dict:
        memory_id = params.get("memory_id")
        content = params.get("content")
        expected_version = params.get("expected_version")
        if (
            not isinstance(memory_id, int)
            or not isinstance(content, str)
            or type(expected_version) is not int
            or expected_version < 1
        ):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "memory_id (int) and content (str) required")
        try:
            scope = await _memory_scope(params)
            row = await update_manual_memory(scope, memory_id, content, expected_version)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))
        if row is None:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"memory {memory_id} not found")
        return row.model_dump()

    async def memory_delete(params: dict) -> dict:
        memory_id = params.get("memory_id")
        if not isinstance(memory_id, int):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "memory_id (int) required")
        scope = await _memory_scope(params)
        ok = await forget_manual_memory(scope, memory_id)
        if not ok:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"memory {memory_id} not found")
        return {"deleted": ok}

    async def memory_profile_update(params: dict) -> dict:
        if params.get("system_preset_id") != "companion" or "session_id" in params:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "system_preset_id must be companion")
        changes = params.get("changes")
        if not isinstance(changes, list) or len(changes) > 5:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "changes must be a list of up to five registered profile fields")
        try:
            validated = [MemoryProfileChange.model_validate(change) for change in changes]
            scope = await _memory_scope(params)
            rows = await update_profile(scope, validated)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return {"memories": [row.model_dump() for row in rows], "system_preset_id": "companion"}

    register("memory.list", memory_list)

    register("memory.update", memory_update)

    register("memory.delete", memory_delete)

    register("memory.profile.update", memory_profile_update)
