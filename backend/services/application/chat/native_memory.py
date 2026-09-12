import json
from datetime import datetime
from typing import Any

from components import session_scope, tool_error, utc_now

from services.contracts.memory import MemoryScope, MemorySource
from services.domains.memory import (
    MemoryDecisions,
    MemoryReviewContext,
    assess_memory_changes,
    embed_memory_text,
    load_review_context,
    retrieve_hybrid_memories,
)
from services.infrastructure.llm import resolve_user_llm_config


class NativeMemory:
    def __init__(self, scope: MemoryScope, *, source: MemorySource) -> None:
        self._scope = scope
        self._source = source
        self._inspection: MemoryReviewContext | None = None

    async def execute_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if {"scope", "user_id", "system_preset_id", "source_refs", "source_kind", "content_version"} & args.keys():
            return tool_error("Memory ownership and provenance are server-controlled")
        try:
            if tool_name == "memory_inspect":
                async with session_scope() as db:
                    self._inspection = await load_review_context(
                        db,
                        self._scope,
                        session_id=self._source.session_id,
                        query=args.get("query"),
                        before_memory_id=args.get("before_memory_id"),
                    )
                payload = self._inspection.payload()
                payload["next_before_memory_id"] = min((r["id"] for r in self._inspection.memories), default=None)
                payload["maintenance_only_memories"] = [
                    r
                    for r in self._inspection.memories
                    if r["status"] == "active"
                    and (not r["expires_at"] or datetime.fromisoformat(r["expires_at"]) > utc_now())
                ]
                return json.dumps(payload, ensure_ascii=False)
            if tool_name == "memory_retain":
                if self._inspection is None:
                    return tool_error("Call memory_inspect first to obtain original evidence and current versions")
                proposal = MemoryDecisions.model_validate(args)
                async with session_scope() as db:
                    config = await resolve_user_llm_config(db, self._scope.user_id)
                rows = await assess_memory_changes(
                    self._scope,
                    self._source,
                    self._inspection,
                    llm_config=config,
                    proposal=proposal.model_dump(),
                )
                self._inspection = None
                return json.dumps(
                    {
                        "result": "reviewed",
                        "changes": [
                            r if r["status"] == "active" else {"id": r["id"], "status": r["status"]} for r in rows
                        ],
                        "note": "No changes means nothing new was retained. Do not claim a candidate is an established fact.",
                    },
                    ensure_ascii=False,
                )
            if tool_name == "memory_recall":
                query = args.get("query")
                if not isinstance(query, str) or not query.strip():
                    return tool_error("query is required")
                vector = await embed_memory_text(self._scope.user_id, query)
                async with session_scope() as db:
                    rows = await retrieve_hybrid_memories(db, self._scope, query, query_embedding=vector)
                return json.dumps({"memories": rows}, default=str, ensure_ascii=False)
            return tool_error(f"Unknown memory tool: {tool_name}")
        except Exception as exc:
            return tool_error(f"Memory operation did not complete: {exc}")
