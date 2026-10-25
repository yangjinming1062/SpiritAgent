"""记忆备份；向量与日记索引从权威内容重建。"""

import hashlib
from typing import Any, get_args
from uuid import UUID

from infrastructure.backup import BackupContext, BackupRows, BackupTable
from runtime.memory_scope import MemoryScope, MemorySource, validate_memory_scope
from sqlalchemy import Select, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.evidence import original_user_messages

from .contracts import MemoryRecord
from .memory_namespaces import MEMORY_KINDS
from .models import Memory, MemoryEvidenceRow, MemoryFingerprint, MemoryRevision


def _prepare_memory(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    validate_memory_scope(MemoryScope(context.user_id, payload.get("system_preset_id")))
    UUID(payload["source_key"])
    if payload.get("source_event_id") is not None and payload["system_preset_id"] != "companion":
        raise ValueError("Life facts belong to the companion preset")
    if payload.get("source_kind") not in get_args(MemorySource.__annotations__["kind"]):
        raise ValueError("Invalid memory source kind")
    if payload.get("status") not in {"active", "candidate", "invalidated", "forgotten"}:
        raise ValueError("Invalid memory status")
    if payload.get("basis") not in {"explicit", "inferred", "observed", "system"}:
        raise ValueError("Invalid memory basis")
    if payload.get("usage") not in {"contextual", "background"}:
        raise ValueError("Invalid memory usage")
    if payload.get("kind") not in MEMORY_KINDS or payload["kind"] == "diary":
        raise ValueError("Invalid memory kind; diary indexes are rebuilt from entries")
    if not isinstance(payload.get("tags"), list) or not all(isinstance(tag, str) for tag in payload["tags"]):
        raise ValueError("Memory tags must be strings")
    if payload.get("status") == "forgotten" and payload.get("content"):
        raise ValueError("Forgotten memory cannot contain original content")


def _query(user_id: int) -> Select[tuple[Memory]]:
    return select(Memory).where(Memory.user_id == user_id, Memory.kind != "diary")


async def _clear(db: AsyncSession, user_id: int) -> None:
    await db.execute(delete(Memory).where(Memory.user_id == user_id, Memory.kind != "diary"))


async def _restore_context(db: AsyncSession, rows: BackupRows, context: BackupContext) -> None:
    originals = {str(row["id"]): row for row in rows.get("memories", [])}
    inserted = {key for table, key in context.rewriter.inserted_rows if table == "memories"}
    messages = {str(row["id"]): row for row in rows.get("messages", [])}
    conversations = {str(row["id"]): row for row in rows.get("conversations", [])}
    invalid = set()
    for table in ("memory_evidence", "memory_revisions"):
        for raw in rows.get(table, []):
            memory = originals.get(str(raw["memory_id"]))
            if memory is not None and memory["status"] == "forgotten":
                raise ValueError("Forgotten memory cannot retain evidence or revisions")
    for raw in rows.get("memory_evidence", []):
        if str(raw["memory_id"]) not in inserted:
            continue
        memory = originals.get(str(raw["memory_id"]))
        if memory is None:
            raise ValueError("Memory evidence lacks its memory")
        message = messages.get(str(raw.get("message_id")))
        conversation = conversations.get(str(message["conversation_id"])) if message else None
        if conversation and conversation["system_preset_id"] != memory["system_preset_id"]:
            raise ValueError("Memory evidence belongs to another preset")
        if message and (message["role"] != "user" or message.get("subtype") is not None):
            raise ValueError("Memory evidence must refer to an original user message")
        if not message or not conversation:
            mapped = context.id_map.get("memories", {}).get(str(raw["memory_id"]))
            if mapped is not None:
                invalid.add(mapped)
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for raw in rows.get("memory_evidence", []):
        memory = originals.get(str(raw["memory_id"]))
        if memory is not None and str(raw["memory_id"]) in inserted:
            by_scope.setdefault(memory["system_preset_id"], []).append(raw)
    for preset, evidence_rows in by_scope.items():
        mapped_ids = {
            int(mapped)
            for raw in evidence_rows
            if (mapped := context.id_map.get("messages", {}).get(str(raw.get("message_id")))) is not None
        }
        source_rows = await original_user_messages(db, context.user_id, preset, mapped_ids) if mapped_ids else {}
        for raw in evidence_rows:
            mapped_id = context.id_map.get("messages", {}).get(str(raw.get("message_id")))
            source = source_rows.get(int(mapped_id)) if mapped_id is not None else None
            fingerprint = (
                hashlib.sha256(f"{source.created_at.isoformat()}\n{source.content}".encode()).hexdigest()
                if source
                else None
            )
            if source is None or raw["quote"] not in source.content or fingerprint != raw["fingerprint"]:
                invalid.add(context.id_map["memories"][str(raw["memory_id"])])
    suppressed: dict[str, set[str]] = {}
    for raw in rows.get("memory_fingerprints", []):
        validate_memory_scope(MemoryScope(context.user_id, raw["system_preset_id"]))
        memory = originals.get(str(raw.get("memory_id")))
        if memory is not None and memory["system_preset_id"] != raw["system_preset_id"]:
            raise ValueError("Memory fingerprint belongs to another preset")
        if not isinstance(raw["suppressed"], bool):
            raise ValueError("Invalid memory fingerprint suppression state")
        if raw["suppressed"]:
            suppressed.setdefault(raw["system_preset_id"], set()).add(raw["fingerprint"])
    for preset, fingerprints in suppressed.items():
        await db.execute(
            update(MemoryFingerprint)
            .where(
                MemoryFingerprint.user_id == context.user_id,
                MemoryFingerprint.system_preset_id == preset,
                MemoryFingerprint.fingerprint.in_(fingerprints),
            )
            .values(suppressed=True)
        )
    if invalid:
        await db.execute(
            update(Memory)
            .where(
                Memory.user_id == context.user_id, Memory.id.in_(invalid), Memory.status.in_(("active", "candidate"))
            )
            .values(
                status="invalidated",
                reason="Original evidence is unavailable after restoration",
                updated_at=Memory.updated_at,
            )
        )
    for raw in rows.get("memories", []):
        if str(raw["id"]) not in inserted:
            continue
        source = conversations.get(str(raw.get("source_conversation_id")))
        if source is not None and source["system_preset_id"] != raw["system_preset_id"]:
            raise ValueError("Memory source belongs to another preset")


def _prepare_revision(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    snapshot = MemoryRecord.model_validate(payload["snapshot"])
    if snapshot.status == "forgotten":
        raise ValueError("Forgotten memory revisions must not be retained")
    snapshot.id = payload["memory_id"]
    for evidence in snapshot.evidence:
        if evidence.message_id is not None:
            mapped = context.id_map.get("messages", {}).get(str(evidence.message_id))
            evidence.message_id = int(mapped) if mapped is not None else None
            evidence.source_unavailable = True if mapped is None else None
    payload["snapshot"] = snapshot.model_dump(mode="json")


def _evidence_query(user_id: int) -> Select[tuple[MemoryEvidenceRow]]:
    return (
        select(MemoryEvidenceRow)
        .join(Memory, Memory.id == MemoryEvidenceRow.memory_id)
        .where(MemoryEvidenceRow.user_id == user_id, Memory.kind != "diary")
    )


def _revision_query(user_id: int) -> Select[tuple[MemoryRevision]]:
    return (
        select(MemoryRevision)
        .join(Memory, Memory.id == MemoryRevision.memory_id)
        .where(MemoryRevision.user_id == user_id, Memory.kind != "diary")
    )


def _fingerprint_query(user_id: int) -> Select[tuple[MemoryFingerprint]]:
    return (
        select(MemoryFingerprint)
        .outerjoin(Memory, Memory.id == MemoryFingerprint.memory_id)
        .where(MemoryFingerprint.user_id == user_id, or_(MemoryFingerprint.memory_id.is_(None), Memory.kind != "diary"))
    )


async def _find_memory(
    db: AsyncSession, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext
) -> int | None:
    key = ("memory_import_keys", context.user_id, context.import_batch_id)
    indexes = db.info.get(key)
    if indexes is None:
        rows = (
            await db.execute(
                select(Memory.id, Memory.source_key, Memory.system_preset_id, Memory.kind, Memory.slot_key).where(
                    Memory.user_id == context.user_id
                )
            )
        ).all()
        indexes = (
            {row.source_key: (row.id, row.system_preset_id) for row in rows},
            {(row.system_preset_id, row.kind, row.slot_key): row.id for row in rows if row.slot_key is not None},
        )
        db.info[key] = indexes
    sources, slots = indexes
    if existing := sources.get(payload["source_key"]):
        if existing[1] != payload["system_preset_id"]:
            raise ValueError("Imported memory source belongs to another preset")
        return existing[0]
    return (
        slots.get((payload["system_preset_id"], payload["kind"], payload["slot_key"]))
        if payload["slot_key"] is not None
        else None
    )


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "memories",
            Memory,
            foreign_keys={"source_conversation_id": "conversations", "source_event_id": "life_events"},
            export_query=_query,
            prepare_payload=_prepare_memory,
            custom_delete=_clear,
            find_existing=_find_memory,
            restore_context=_restore_context,
        ),
        BackupTable(
            "memory_evidence",
            MemoryEvidenceRow,
            export_query=_evidence_query,
            foreign_keys={"memory_id": "memories", "message_id": "messages"},
            unique_keys=("memory_id", "fingerprint", "quote", "stance"),
            skip_existing_parent="memory_id",
        ),
        BackupTable(
            "memory_revisions",
            MemoryRevision,
            export_query=_revision_query,
            foreign_keys={"memory_id": "memories"},
            unique_keys=("memory_id", "content_version"),
            prepare_payload=_prepare_revision,
            skip_existing_parent="memory_id",
        ),
        BackupTable(
            "memory_fingerprints",
            MemoryFingerprint,
            export_query=_fingerprint_query,
            foreign_keys={"memory_id": "memories"},
            unique_keys=("memory_id", "fingerprint"),
        ),
    )
