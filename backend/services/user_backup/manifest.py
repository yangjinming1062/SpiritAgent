import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from components import utc_now
from fastapi import HTTPException

if TYPE_CHECKING:
    from modules.auth import User


MANIFEST_FORMAT = "spiritagent-user-backup"
MANIFEST_SCHEMA_VERSION = 2
# pgvector 当前固定 1536 维；切到其它 embedding 模型需 bump schema_version。
EMBEDDING_DIM_DEFAULT = 1536


def build_manifest(user: "User", rows_by_table: dict[str, list[dict[str, Any]]], exported_by: str) -> dict[str, Any]:
    return {
        "format": MANIFEST_FORMAT,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "exported_at": utc_now().isoformat(),
        "exported_by": f"admin:{exported_by}",
        "source_user_id": user.id,
        "source_username": user.username,
        "embedding_dim": EMBEDDING_DIM_DEFAULT,
        "tables": list(rows_by_table.keys()),
        "row_counts": {tbl: len(rs) for tbl, rs in rows_by_table.items()},
    }


def validate_manifest(payload: dict[str, Any] | None) -> None:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="manifest.json must be a JSON object")
    if payload.get("format") != MANIFEST_FORMAT:
        raise HTTPException(status_code=400, detail=f"Unknown backup format: {payload.get('format')!r}")
    schema_version = payload.get("schema_version")
    if schema_version not in (1, MANIFEST_SCHEMA_VERSION):
        raise HTTPException(status_code=400, detail=f"Unsupported schema_version: {schema_version!r}")
    if not isinstance(payload.get("source_user_id"), int):
        raise HTTPException(status_code=400, detail="manifest.source_user_id must be an integer")
    if not isinstance(payload.get("tables"), list):
        raise HTTPException(status_code=400, detail="manifest.tables must be a list")
    from .serializers import CONVERSATION_TABLES, TABLES

    tables = payload["tables"]
    if any(not isinstance(table, str) or table not in TABLES for table in tables) or len(set(tables)) != len(tables):
        raise HTTPException(status_code=400, detail="Unknown or duplicate backup tables")
    if set(tables) & CONVERSATION_TABLES and not CONVERSATION_TABLES.issubset(tables):
        raise HTTPException(status_code=400, detail="Conversations and messages must be restored together")
    if schema_version == MANIFEST_SCHEMA_VERSION and not (set(TABLES) - CONVERSATION_TABLES).issubset(tables):
        raise HTTPException(status_code=400, detail="Backup is missing companion data tables")


def load_manifest(extract_root: Path) -> dict[str, Any]:
    manifest_path = extract_root / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=400, detail="Zip must contain manifest.json")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid manifest.json: {exc}") from exc
    validate_manifest(payload)
    if payload["schema_version"] == MANIFEST_SCHEMA_VERSION:
        checksums = payload.get("checksums")
        if not isinstance(checksums, dict):
            raise HTTPException(status_code=400, detail="Backup checksums are missing")
        actual = {
            path.relative_to(extract_root).as_posix()
            for path in extract_root.rglob("*")
            if path.is_file() and path != manifest_path
        }
        if actual != set(checksums):
            raise HTTPException(status_code=400, detail="Backup file inventory does not match manifest")
        for name, expected in checksums.items():
            with (extract_root / name).open("rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
            if digest != expected:
                raise HTTPException(status_code=400, detail=f"Backup checksum mismatch: {name}")
    return payload
