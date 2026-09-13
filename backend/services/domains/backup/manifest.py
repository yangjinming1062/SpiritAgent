import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from components import utc_now
from modules.memory import MEMORY_EMBEDDING_DIM

from .serializers import CONVERSATION_TABLES, TABLES

if TYPE_CHECKING:
    from modules.auth import User


MANIFEST_FORMAT = "spiritagent-user-backup"


def build_manifest(user: "User", rows_by_table: dict[str, list[dict[str, Any]]], exported_by: str) -> dict[str, Any]:
    return {
        "format": MANIFEST_FORMAT,
        "exported_at": utc_now().isoformat(),
        "exported_by": f"admin:{exported_by}",
        "source_user_id": user.id,
        "source_username": user.username,
        "embedding_dim": MEMORY_EMBEDDING_DIM,
        "tables": list(rows_by_table.keys()),
        "row_counts": {tbl: len(rs) for tbl, rs in rows_by_table.items()},
    }


def validate_manifest(payload: dict[str, Any] | None) -> None:
    if not isinstance(payload, dict):
        raise ValueError("manifest.json must be a JSON object")
    if payload.get("format") != MANIFEST_FORMAT:
        raise ValueError(f"Unknown backup format: {payload.get('format')!r}")
    if not isinstance(payload.get("source_user_id"), int):
        raise ValueError("manifest.source_user_id must be an integer")
    if not isinstance(payload.get("tables"), list):
        raise ValueError("manifest.tables must be a list")
    tables = payload["tables"]
    if any(not isinstance(table, str) or table not in TABLES for table in tables) or len(set(tables)) != len(tables):
        raise ValueError("Unknown or duplicate backup tables")
    if set(tables) & CONVERSATION_TABLES and not CONVERSATION_TABLES.issubset(tables):
        raise ValueError("Conversations and messages must be restored together")
    if not (set(TABLES) - CONVERSATION_TABLES).issubset(tables):
        raise ValueError("Backup is missing companion data tables")


def load_manifest(extract_root: Path) -> dict[str, Any]:
    manifest_path = extract_root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("Zip must contain manifest.json")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid manifest.json: {exc}") from exc
    validate_manifest(payload)
    checksums = payload.get("checksums")
    if not isinstance(checksums, dict):
        raise ValueError("Backup checksums are missing")
    actual = {
        path.relative_to(extract_root).as_posix()
        for path in extract_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != set(checksums):
        raise ValueError("Backup file inventory does not match manifest")
    for name, expected in checksums.items():
        with (extract_root / name).open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        if digest != expected:
            raise ValueError(f"Backup checksum mismatch: {name}")
    return payload
