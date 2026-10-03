import json
from datetime import date, datetime
from typing import Any

from components import get_logger, session_scope, tool_error, utc_now
from sqlalchemy.exc import SQLAlchemyError

from services.contracts import MemoryScope, MemorySource
from services.domains.memory import (
    MemoryDecisions,
    MemoryReviewContext,
    assess_memory_changes,
    embed_memory_text,
    load_review_context,
    retrieve_hybrid_memories,
)
from services.infrastructure.llm import LLMRuntimeError, resolve_user_llm_config

logger = get_logger(__name__)

# 记忆 ID 是 int4 主键。
_MEMORY_ID_MAX = 2**31 - 1


def _is_memory_id(value: object) -> bool:
    """页游标由模型给出：只接受 int4 范围内的正整数，bool、浮点、字符串与越界值不进入 SQL 条件。"""
    return type(value) is int and 1 <= value <= _MEMORY_ID_MAX


class NativeMemory:
    def __init__(self, scope: MemoryScope, *, source: MemorySource) -> None:
        self._scope = scope
        self._source = source
        self._inspection: MemoryReviewContext | None = None

    async def execute_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        try:
            if tool_name == "memory_inspect":
                query = args.get("query")
                before_memory_id = args.get("before_memory_id")
                if query is not None and not isinstance(query, str):
                    return tool_error("query must be a string")
                if before_memory_id is not None and not _is_memory_id(before_memory_id):
                    return tool_error(
                        "before_memory_id must be a positive integer memory ID, such as next_before_memory_id from the previous result",
                    )
                async with session_scope() as db:
                    self._inspection = await load_review_context(
                        db,
                        self._scope,
                        session_id=self._source.session_id,
                        query=query,
                        before_memory_id=before_memory_id,
                    )
                payload = self._inspection.payload()
                payload["next_before_memory_id"] = min((r.id for r in self._inspection.memories), default=None)
                payload["maintenance_only_memories"] = [
                    r.model_dump()
                    for r in self._inspection.memories
                    if r.status == "active" and (not r.expires_at or datetime.fromisoformat(r.expires_at) > utc_now())
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
                            r.model_dump() if r.status == "active" else {"id": r.id, "status": r.status} for r in rows
                        ],
                        "note": "No changes means nothing new was retained. Do not claim a candidate is an established fact.",
                    },
                    ensure_ascii=False,
                )
            if tool_name == "memory_recall":
                query = args.get("query")
                if query is not None and not isinstance(query, str):
                    return tool_error("query must be a string")
                query = query or ""
                raw_date = args.get("diary_date")
                diary_date = None
                if raw_date is not None:
                    if not isinstance(raw_date, str):
                        return tool_error("diary_date must be YYYY-MM-DD")
                    diary_date = date.fromisoformat(raw_date)
                    if diary_date.isoformat() != raw_date:
                        return tool_error("diary_date must be YYYY-MM-DD")
                if not query.strip() and diary_date is None:
                    return tool_error("Provide query or diary_date")
                vector = await embed_memory_text(self._scope.user_id, query) if diary_date is None else None
                async with session_scope() as db:
                    rows = await retrieve_hybrid_memories(
                        db,
                        self._scope,
                        query,
                        query_embedding=vector,
                        diary_date=diary_date,
                    )
                return json.dumps({"memories": [row.model_dump() for row in rows]}, default=str, ensure_ascii=False)
            return tool_error(f"Unknown memory tool: {tool_name}")
        except (ValueError, LLMRuntimeError) as exc:
            # 校验失败与已脱敏的 LLM 失败，原因交给模型决定纠正或放弃。
            logger.warning("Memory tool failed", extra={"tool_name": tool_name}, exc_info=True)
            return tool_error(f"Memory operation did not complete: {exc}")
        except SQLAlchemyError:
            # 语句与参数只进服务端日志，不作为工具结果交给模型。
            logger.exception("Database error executing memory tool", extra={"tool_name": tool_name})
            return tool_error("Memory operation did not complete because of a temporary storage error.")
        except Exception:
            # 其余异常的文本可能含连接地址或路径，同样只进日志。
            logger.exception("Memory tool failed", extra={"tool_name": tool_name})
            return tool_error("Memory operation did not complete because of an internal error.")
