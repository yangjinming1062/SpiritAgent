import asyncio
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from common import ModelBase
from components import ensure_utc
from modules.auth import User, UserModelConfig
from modules.companion import (
    AvatarAsset,
    Companion2DModel,
    Companion3DModel,
    CompanionDiaryEntry,
    CompanionMoment,
    CompanionOutfit,
    CompanionRoomBackdrop,
    Persona,
)
from modules.conversation import Conversation, Message
from modules.memory import Memory
from modules.scheduler import CronJob
from modules.settings import UserSetting
from sqlalchemy import Date, DateTime, select
from sqlalchemy.ext.asyncio import AsyncSession

from .file_packing import UrlRewriter

# 表白名单与依赖顺序单源维护；列从模型读取，新增持久字段不会静默漏备份。
TABLE_MODELS: dict[str, type[ModelBase]] = {
    "user_preferences": User,
    "conversations": Conversation,
    "user_model_configs": UserModelConfig,
    "avatar_assets": AvatarAsset,
    "companion_outfits": CompanionOutfit,
    "companion_room_backdrops": CompanionRoomBackdrop,
    "personas": Persona,
    "companion_2d_models": Companion2DModel,
    "companion_3d_models": Companion3DModel,
    "user_settings": UserSetting,
    "cron_jobs": CronJob,
    "memories": Memory,
    "companion_moments": CompanionMoment,
    "companion_diary_entries": CompanionDiaryEntry,
    "messages": Message,
}
TABLES = tuple(TABLE_MODELS)
CONVERSATION_TABLES = frozenset({"conversations", "messages"})
FOREIGN_KEYS: dict[str, dict[str, str]] = {
    "personas": {"active_backdrop_id": "companion_room_backdrops"},
    "companion_2d_models": {"avatar_id": "avatar_assets", "outfit_id": "companion_outfits"},
    "companion_3d_models": {"source_portrait_id": "avatar_assets"},
    "cron_jobs": {"conversation_id": "conversations"},
    "companion_moments": {"memory_id": "memories", "session_id": "conversations"},
    "messages": {"conversation_id": "conversations"},
}
UNIQUE_KEYS: dict[str, tuple[str, ...]] = {
    "personas": (),
    "user_model_configs": (),
    "user_settings": ("setting_key",),
    "companion_diary_entries": ("entry_date",),
}
IdMap = dict[str, dict[str, int | str]]


def _columns(table: str) -> list[str]:
    if table == "user_preferences":
        return ["nightly_activity_enabled"]
    return [column.name for column in TABLE_MODELS[table].__table__.columns if column.name != "user_id"]


async def serialize_rows(
    db: AsyncSession,
    table: str,
    user_id: int,
    *,
    batch_size: int = 1000,
) -> list[dict[str, Any]]:
    model = TABLE_MODELS[table]
    columns = _columns(table)
    if table == "messages":
        base_stmt = select(Message).join(Conversation).where(Conversation.user_id == user_id)
    else:
        base_stmt = select(model).where((User.id if table == "user_preferences" else model.user_id) == user_id)

    result: list[dict[str, Any]] = []
    offset = 0
    while True:
        stmt = base_stmt.order_by(model.id).offset(offset).limit(batch_size)
        rows = (await db.execute(stmt)).scalars().all()
        if not rows:
            break
        for row in rows:
            payload = {col: getattr(row, col) for col in columns}
            for col, value in payload.items():
                if isinstance(value, datetime):
                    payload[col] = ensure_utc(value).isoformat()
                elif isinstance(value, date):
                    payload[col] = value.isoformat()
            if table == "memories" and payload.get("embedding") is not None:
                payload["embedding"] = list(map(float, payload["embedding"]))
            result.append(payload)
        if len(rows) < batch_size:
            break
        offset += len(rows)
        await asyncio.sleep(0)
    return result


async def insert_rows(
    db: AsyncSession,
    table: str,
    raw_rows: list[dict[str, Any]],
    target_user_id: int,
    rewriter: UrlRewriter,
    id_map: IdMap,
    *,
    mode: str,
) -> tuple[dict[str, int | str], int]:
    model = TABLE_MODELS[table]
    new_map: dict[str, int | str] = {}
    inserted = 0
    parents: list[tuple[Conversation, Any, datetime | None]] = []
    for raw in raw_rows:
        payload = _build_payload(table, raw, target_user_id, rewriter, id_map)
        if table == "user_preferences":
            if mode == "overwrite":
                user = await db.get(User, target_user_id)
                user.nightly_activity_enabled = payload["nightly_activity_enabled"]
                inserted += 1
            continue
        existing = None
        if mode == "merge" and table in UNIQUE_KEYS:
            existing = await db.scalar(
                select(model).where(
                    model.user_id == target_user_id,
                    *(getattr(model, key) == payload[key] for key in UNIQUE_KEYS[table]),
                ),
            )
        if (
            mode == "merge"
            and table == "memories"
            and (payload.get("context") or "").startswith(
                ("user_profile:", "auto_inject:", "inferred_profile:", "diary:"),
            )
        ):
            existing = await db.scalar(
                select(Memory).where(Memory.user_id == target_user_id, Memory.context == payload["context"]),
            )
        if table == "conversations" and payload.get("kind") == "special" and payload.get("system_preset_id"):
            existing = await db.scalar(
                select(Conversation).where(
                    Conversation.user_id == target_user_id,
                    Conversation.kind == "special",
                    Conversation.system_preset_id == payload["system_preset_id"],
                ),
            )
        if existing is not None:
            new_map[str(raw["id"])] = existing.id
            continue
        if (
            mode == "merge"
            and payload.get("active")
            and await db.scalar(
                select(model.id).where(model.user_id == target_user_id, model.active.is_(True)).limit(1),
            )
        ):
            payload["active"] = False
        instance = model(**payload)
        db.add(instance)
        await db.flush()
        new_map[str(raw["id"])] = instance.id
        inserted += 1
        if table == "conversations":
            parents.append((instance, raw.get("parent_id"), payload.get("updated_at")))
    for conversation, parent_id, updated_at in parents:
        if parent_id is not None:
            if str(parent_id) not in new_map:
                raise ValueError("Conversation parent is missing from backup")
            conversation.parent_id = new_map[str(parent_id)]
            await db.flush()
            if updated_at is not None:
                conversation.updated_at = updated_at
    await db.flush()
    return new_map, inserted


def _build_payload(
    table: str,
    raw: dict[str, Any],
    user_id: int,
    rewriter: UrlRewriter,
    id_map: IdMap,
) -> dict[str, Any]:
    model = TABLE_MODELS[table]
    payload = {key: value for key, value in raw.items() if key in _columns(table) and key != "id"}
    for column in model.__table__.columns:
        value = payload.get(column.name)
        if value is None:
            continue
        if isinstance(column.type, DateTime):
            payload[column.name] = ensure_utc(datetime.fromisoformat(value))
        elif isinstance(column.type, Date):
            payload[column.name] = date.fromisoformat(value)
    payload = rewriter.rewrite(payload)
    for key, ref_table in FOREIGN_KEYS.get(table, {}).items():
        value = raw.get(key)
        mapped = id_map.get(ref_table, {}).get(str(value)) if value is not None else None
        if (
            value is not None
            and ref_table in id_map
            and mapped is None
            and key not in {"avatar_id", "source_portrait_id"}
        ):
            raise ValueError(f"Missing {ref_table} reference in {table}.{key}")
        payload[key] = mapped
    if table == "messages" and payload.get("conversation_id") is None:
        raise ValueError("Message conversation is missing from backup")
    if table == "conversations":
        payload["parent_id"] = None
    if table == "companion_room_backdrops":
        payload["outfit_fingerprint"] = str(
            id_map.get("companion_outfits", {}).get(str(raw.get("outfit_fingerprint")), ""),
        )
    if table == "companion_diary_entries":
        for key, ref in (("memory_ids", "memories"), ("moment_ids", "companion_moments")):
            payload[key] = [
                str(id_map[ref][str(value)]) for value in raw.get(key, []) if str(value) in id_map.get(ref, {})
            ]
    if table == "companion_2d_models" and payload.get("manifest_json"):
        manifest = payload["manifest_json"]
        payload["content_hash"] = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
        rewriter.write_manifest(payload.get("manifest_path"), manifest)
    if table == "cron_jobs" and "kind" not in payload:
        payload["kind"] = "special"
    if table not in {"messages", "user_preferences"}:
        payload["user_id"] = user_id
    return payload


def deserialize_rows(extract_root: Path, tables: list[str]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        if table not in tables:
            continue
        payload = json.loads((extract_root / "db" / f"{table}.json").read_text(encoding="utf-8"))
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid rows in {table}.json")
        if any("id" not in row for row in rows) and table != "user_preferences":
            raise ValueError(f"Missing row id in {table}.json")
        if table != "user_preferences" and len({str(row["id"]) for row in rows}) != len(rows):
            raise ValueError(f"Duplicate row id in {table}.json")
        if table == "user_preferences" and len(rows) > 1:
            raise ValueError("Backup contains multiple user preference rows")
        result[table] = rows
    return result
