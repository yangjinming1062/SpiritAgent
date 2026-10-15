"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any, get_args

from infrastructure.backup import BackupContext, BackupRows, BackupTable, IdMap
from runtime.memory_scope import MemoryScope, MemorySource, validate_memory_scope
from sqlalchemy import Select, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import MEMORY_EMBEDDING_DIM, MEMORY_SLOT_CONTEXT_PREFIXES, Memory

_MEMORY_SOURCE_KINDS = frozenset((*get_args(MemorySource.__annotations__["kind"]), "import"))


def _original_source(refs: dict[str, Any]) -> Any:
    return refs.get("original_source", refs)


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    user_id = context.user_id
    if table == "memories":
        if (payload.get("context") or "").startswith("diary:") or payload.get("source_kind") == "diary":
            raise ValueError("Diary indexes must be rebuilt from published diaries")
        validate_memory_scope(MemoryScope(user_id, payload.get("system_preset_id")))
        if not isinstance(payload.get("content_version"), int) or payload["content_version"] <= 0:
            raise ValueError("Invalid memory content version")
        if not isinstance(payload.get("source_refs"), dict) or not isinstance(payload.get("source_kind"), str):
            raise ValueError("Memory source is required")
        if payload.get("status") not in {"active", "candidate", "invalidated", "forgotten"}:
            raise ValueError("Invalid memory status")
        if payload.get("basis") not in {"explicit", "inferred", "observed", "system"}:
            raise ValueError("Invalid memory basis")
        if payload.get("usage") not in {"background", "contextual"}:
            raise ValueError("Invalid memory usage")
        if not isinstance(payload.get("evidence"), list) or not isinstance(payload.get("history"), list):
            raise ValueError("Memory evidence and history are required")
        embedding = payload.get("embedding")
        model_key = payload.get("embedding_model_key")
        if model_key is not None and (
            not isinstance(model_key, str)
            or len(model_key) != 64
            or any(char not in "0123456789abcdef" for char in model_key)
        ):
            raise ValueError("Memory embedding model key is invalid")
        if embedding is not None:
            if not isinstance(embedding, list) or not all(isinstance(v, int | float) for v in embedding):
                raise ValueError("Memory embedding must be a list of numbers")
            if len(embedding) != MEMORY_EMBEDDING_DIM:
                raise ValueError(f"Memory embedding dim {len(embedding)} != {MEMORY_EMBEDDING_DIM}")
        if payload["source_kind"] not in _MEMORY_SOURCE_KINDS:
            raise ValueError("Invalid memory source kind")
        payload["source_refs"] = {
            "imported_memory_id": raw["id"],
            "original_source": _original_source(payload["source_refs"]),
        }


async def restore_memory_context(
    db: AsyncSession,
    rows: dict[str, list[dict[str, Any]]],
    id_map: IdMap,
    user_id: int,
    import_batch_id: str,
) -> None:
    conversations = {str(row["id"]): row for row in rows.get("conversations", [])}
    messages = {str(row["id"]): row for row in rows.get("messages", [])}
    for raw in rows.get("memories", []):
        memory = await db.get(Memory, int(id_map["memories"][str(raw["id"])]))
        # 只处理本批插入的记录；merge 命中的现有槽位保留原来源。
        if memory.source_refs.get("import_batch_id") != import_batch_id:
            continue
        refs = raw["source_refs"]
        restored: dict[str, Any] = {
            "imported_memory_id": raw["id"],
            "import_batch_id": import_batch_id,
            "original_source": _original_source(refs),
        }
        session_id = refs.get("session_id")
        if session_id is not None and str(session_id) in conversations:
            conv = conversations[str(session_id)]
            if conv["system_preset_id"] != raw["system_preset_id"]:
                raise ValueError("Memory source belongs to a different preset")
            restored["session_id"] = int(id_map["conversations"][str(session_id)])
            for mid in refs.get("message_ids", []):
                message = messages.get(str(mid))
                if message is not None and str(message["conversation_id"]) != str(session_id):
                    raise ValueError("Invalid memory source message")

        def remap_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
            result = []
            for item in evidence:
                entry = dict(item)
                mid = entry.get("message_id")
                if mid is not None:
                    message = messages.get(str(mid))
                    conv = conversations.get(str(message["conversation_id"])) if message else None
                    if conv and conv["system_preset_id"] != raw["system_preset_id"]:
                        raise ValueError("Memory evidence belongs to a different preset")
                    if not message or not conv or str(mid) not in id_map.get("messages", {}):
                        entry.pop("message_id", None)
                        entry.pop("session_id", None)
                        entry["source_unavailable"] = True
                    else:
                        if message["role"] != "user" or message.get("subtype") is not None:
                            raise ValueError("Memory evidence must reference an original user message")
                        entry["message_id"] = int(id_map["messages"][str(mid)])
                        entry["session_id"] = int(id_map["conversations"][str(message["conversation_id"])])
                result.append(entry)
            return result

        memory.evidence = remap_evidence(raw["evidence"])
        if any(e.get("source_unavailable") for e in memory.evidence) and memory.status in {"active", "candidate"}:
            memory.status = "invalidated"
            memory.reason = "Original evidence is unavailable after restoration"
        history = []
        for old in raw["history"]:
            item = dict(old)
            item["id"] = memory.id
            item["evidence"] = remap_evidence(item.get("evidence", []))
            history.append(item)
        memory.history = history
        restored["message_ids"] = sorted({e["message_id"] for e in memory.evidence if "message_id" in e})
        memory.source_refs = restored
        # 赋列表达式使 UPDATE 显式写入原值；否则 onupdate 会把备份中的 updated_at 改成导入时刻。
        memory.updated_at = Memory.updated_at
    await db.flush()


def embedding_dimension() -> int:
    return MEMORY_EMBEDDING_DIM


def _export(payload: dict[str, Any]) -> None:
    if payload.get("embedding") is not None:
        payload["embedding"] = list(map(float, payload["embedding"]))


def _query(user_id: int) -> Select[tuple[Memory]]:
    return select(Memory).where(
        Memory.user_id == user_id,
        Memory.source_kind != "diary",
        or_(Memory.context.is_(None), ~Memory.context.like("diary:%")),
    )


async def _check(db: AsyncSession, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    payload["source_refs"]["import_batch_id"] = context.import_batch_id


async def _find_slot(
    db: AsyncSession,
    raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> int | None:
    if context.mode != "merge" or not (payload.get("context") or "").startswith(MEMORY_SLOT_CONTEXT_PREFIXES):
        return None
    await db.flush()
    return await db.scalar(
        select(Memory.id).where(
            Memory.user_id == context.user_id,
            Memory.system_preset_id == payload["system_preset_id"],
            Memory.context == payload["context"],
        ),
    )


async def clear_diary_indexes(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        delete(Memory).where(
            Memory.user_id == user_id,
            Memory.system_preset_id == "companion",
            or_(Memory.source_kind == "diary", Memory.context.like("diary:%")),
        ),
    )


async def _clear(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        delete(Memory).where(
            Memory.user_id == user_id,
            Memory.source_kind != "diary",
            or_(Memory.context.is_(None), ~Memory.context.like("diary:%")),
        ),
    )


async def _restore_context(db: AsyncSession, rows: BackupRows, context: BackupContext) -> None:
    await restore_memory_context(db, rows, context.id_map, context.user_id, context.import_batch_id)


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "memories",
            Memory,
            export_query=_query,
            export_payload=_export,
            prepare_payload=partial(_prepare, "memories"),
            validate_payload=_check,
            find_existing=_find_slot,
            custom_delete=_clear,
            restore_context=_restore_context,
        ),
    )
