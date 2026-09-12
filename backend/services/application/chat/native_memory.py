import json
from typing import ClassVar

from components import (
    MAX_AUTO_INJECT_CONTENT_CHARS,
    MAX_RECALL_CONTENT_CHARS,
    MEMORY_RECALL_MAX_RESULTS,
    get_logger,
    session_scope,
    tool_error,
)
from modules.memory import Memory
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from services.domains.memory import (
    AUTO_INJECT_SLOTS,
    FORBIDDEN_FROM_LLM,
    RECALL_TAGS,
    backfill_memory_embeddings,
    embed_memory_text,
    normalize_recall_context,
    retrieve_hybrid_memories,
)

logger = get_logger(__name__)


class NativeMemory:
    """单回合 memory 视图；每次数据库操作都使用独立短会话。"""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    def format_for_system_prompt(self, target: str = "") -> str | None:
        return (
            "# Native Memory System\n"
            "Active. All memories are stored securely in the database.\n"
            "Use memory_recall to search recall-pool facts, memory_retain to store new facts, "
            "and memory_forget to delete outdated facts.\n"
            "Recall pool is searchable on demand; auto_inject slots are written via "
            "memory_retain(kind='auto_inject') and are injected into every conversation."
        )

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        handler = self._HANDLERS.get(tool_name)
        if handler is None:
            return tool_error(f"Unknown tool: {tool_name}")
        return await handler(self, args)

    async def _retain(self, args: dict) -> str:
        kind = args.get("kind")
        content = (args.get("content") or "").strip()
        if kind not in ("recall", "auto_inject"):
            return tool_error("kind must be 'recall' or 'auto_inject'")
        if not content:
            return tool_error("content is required")
        if kind == "auto_inject":
            return await self._retain_auto_inject(content, args.get("context"))
        importance = float(args.get("importance", 1.0) or 1.0)
        return await self._retain_recall(content, args.get("tags") or [], args.get("context"), importance=importance)

    async def _retain_auto_inject(self, content: str, context: str | None) -> str:
        if context not in AUTO_INJECT_SLOTS:
            return tool_error(f"auto_inject context must be one of {list(AUTO_INJECT_SLOTS)}, got {context!r}")
        if len(content) > MAX_AUTO_INJECT_CONTENT_CHARS:
            return tool_error(f"auto_inject content exceeds {MAX_AUTO_INJECT_CONTENT_CHARS} chars; trim before writing")
        async with session_scope() as db:
            existing = (
                await db.execute(select(Memory).where(Memory.user_id == self.user_id, Memory.context == context))
            ).scalar_one_or_none()
            if existing is not None:
                existing.content = content
                existing.tags = json.dumps(["auto_inject"])
            else:
                db.add(Memory(user_id=self.user_id, content=content, context=context, tags=json.dumps(["auto_inject"])))
            try:
                await db.commit()
            except IntegrityError:
                # 部分唯一索引上的并发 upsert。
                await db.rollback()
                return tool_error("concurrent auto_inject write; retry")
        return json.dumps({"result": "Auto-inject memory updated.", "context": context})

    async def _retain_recall(self, content: str, tags: list, context: str | None, importance: float = 1.0) -> str:
        if not tags:
            return tool_error("recall requires at least one tag")
        bad = [t for t in tags if t not in RECALL_TAGS]
        if bad:
            return tool_error(f"unknown recall tags {bad}; allowed: {sorted(RECALL_TAGS)}")
        ctx_raw = (context or "").strip()
        if any(ctx_raw.startswith(prefix) for prefix in FORBIDDEN_FROM_LLM):
            return tool_error("recall context cannot use reserved prefixes; those are backend-owned namespaces")
        ctx = normalize_recall_context(ctx_raw)
        imp = max(0.1, min(5.0, float(importance)))
        async with session_scope() as db:
            mem = Memory(
                user_id=self.user_id,
                content=content[:MAX_RECALL_CONTENT_CHARS],
                context=ctx,
                tags=json.dumps(tags),
                importance=imp,
            )
            db.add(mem)
            await db.commit()
            result = json.dumps({"result": "Recall memory stored.", "memory_id": mem.id, "context": ctx})
        await backfill_memory_embeddings(self.user_id, [(mem.id, mem.content)])
        return result

    async def _recall(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("Missing required parameter: query")
        try:
            query_embedding = await embed_memory_text(self.user_id, query)
            async with session_scope() as db:
                results = await retrieve_hybrid_memories(
                    db,
                    self.user_id,
                    query,
                    query_embedding=query_embedding,
                    limit=MEMORY_RECALL_MAX_RESULTS,
                )
            if not results:
                return json.dumps({"result": "No relevant memories found."})
            lines = [
                f"ID: {r['id']}{(' [' + r['context'] + ']') if r.get('context') else ''} - {r['content']}"
                for r in results
            ]
            return json.dumps({"result": "\n".join(lines)})
        except Exception as e:
            logger.error("memory_recall failed", extra={"error": str(e)})
            return tool_error(f"Failed to search memory: {e}")

    async def _forget(self, args: dict) -> str:
        memory_id = args.get("memory_id")
        if not memory_id:
            return tool_error("Missing required parameter: memory_id")
        try:
            async with session_scope() as db:
                mem = (
                    await db.execute(select(Memory).where(Memory.id == memory_id, Memory.user_id == self.user_id))
                ).scalar_one_or_none()
                if not mem:
                    return tool_error(f"Memory with ID {memory_id} not found.")
                await db.delete(mem)
                await db.commit()
            return json.dumps({"result": f"Memory {memory_id} deleted successfully."})
        except Exception as e:
            logger.error("memory_forget failed", extra={"error": str(e)})
            return tool_error(f"Failed to delete memory: {e}")

    _HANDLERS: ClassVar[dict[str, object]] = {
        "memory_retain": _retain,
        "memory_recall": _recall,
        "memory_forget": _forget,
    }
