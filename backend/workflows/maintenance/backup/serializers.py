"""备份类别与引用编排；行的校验、写入和重建由业务贡献者完成。"""

import json
from collections.abc import Sequence
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

from infrastructure.backup import BACKUP_REGISTRY, BACKUP_TABLE_ORDER, BackupContext, BackupImportMode, IdMap
from infrastructure.text import ensure_utc
from sqlalchemy.ext.asyncio import AsyncSession

from .file_packing import UrlRewriter

TABLES = BACKUP_TABLE_ORDER
CONVERSATION_TABLES = frozenset(
    {
        "conversations",
        "messages",
        "companion_topic_batches",
        "companion_topics",
        "companion_topic_feedback",
        "companion_topic_usages",
    },
)
ACTION_TABLES = frozenset({"companion_action_packs", "companion_actions"})
DESKTOP_TABLES = frozenset({"desktop_video_sets", "desktop_video_actions", "desktop_video_states"})
POST_TABLES = frozenset({"companion_posts", "companion_post_comments"})
IDENTITY_TABLES = frozenset({"personas", "avatar_assets", "companion_character_cards"})
# 导入勾选粒度：分组 ID → 表；Admin 与 API 共用，不在页面里再维护表名。
BACKUP_SECTIONS: dict[str, tuple[str, ...]] = {
    "identity": ("personas", "avatar_assets", "companion_character_cards"),
    "conversations": (
        "conversations",
        "custom_voices",
        "companion_topic_batches",
        "companion_topics",
        "companion_topic_feedback",
        "messages",
        "companion_topic_usages",
    ),
    "memories": ("memories",),
    "posts": ("companion_posts", "companion_post_comments"),
    "diary": ("companion_diary_entries",),
    "wardrobe": ("companion_outfits", "companion_action_packs", "companion_actions"),
    "scenes": ("companion_scenes",),
    "desktop_life": ("desktop_video_sets", "desktop_video_actions", "desktop_video_states"),
    "automation": ("cron_jobs", "companion_intents"),
    "settings": ("user_model_configs", "custom_voices", "user_settings", "user_preferences"),
}
BACKUP_SECTION_IDS = tuple(BACKUP_SECTIONS)
# 选中即整组恢复，不允许落成半个身份/会话/视频包。
ATOMIC_SECTION_GROUPS: tuple[frozenset[str], ...] = (
    IDENTITY_TABLES,
    CONVERSATION_TABLES,
    ACTION_TABLES,
    DESKTOP_TABLES,
    POST_TABLES,
)
# 预检时身份三表成组写入的顺序：先头像，再角色卡与人设。
IDENTITY_GROUP: tuple[str, ...] = ("avatar_assets", "companion_character_cards", "personas")
IDENTITY_INCOMPLETE_REASON = "基础身份必须同时包含人设、头像与角色卡。"
IDENTITY_BLOCKED_REASON = "目标现有动作包等仍引用身份，无法安全覆盖基础身份；请一并恢复这些类别，或先处理引用。"
IDENTITY_DEPENDENT_REASON = "缺少可映射的基础身份，无法恢复此类别。"


def tables_for_sections(sections: Sequence[str] | None) -> frozenset[str]:
    """解析导入分组为表集合；None 表示全部，空列表或未知 ID 由调用方拒绝。"""
    if sections is None:
        return frozenset(TABLES)
    normalized = [item.strip() for item in sections if item and item.strip()]
    if not normalized:
        raise ValueError("恢复范围不能为空")
    if "all" in normalized:
        if len(normalized) > 1:
            raise ValueError("恢复范围 all 不能与其他分组同时指定")
        return frozenset(TABLES)
    unknown = [item for item in normalized if item not in BACKUP_SECTIONS]
    if unknown:
        raise ValueError(f"未知的恢复范围：{', '.join(unknown)}")
    tables: set[str] = set()
    for item in normalized:
        tables.update(BACKUP_SECTIONS[item])
    return frozenset(tables)


@cache
def _columns(table: str) -> tuple[str, ...]:
    return tuple(column.name for column in BACKUP_REGISTRY.table(table).columns)


async def serialize_rows(db: AsyncSession, table: str, user_id: int, *, batch_size: int = 1000) -> list[dict[str, Any]]:
    return await BACKUP_REGISTRY.table(table).export(db, user_id, batch_size=batch_size)


def _build_payload(table: str, raw: dict[str, Any], context: BackupContext) -> dict[str, Any]:
    contributor = BACKUP_REGISTRY.table(table)
    columns = _columns(table)
    if unknown := raw.keys() - set(columns):
        raise ValueError(f"Unknown columns in {table}: {', '.join(sorted(unknown))}")
    payload = {key: value for key, value in raw.items() if key != "id"}
    for column in contributor.columns:
        value = payload.get(column.name)
        if value is None:
            continue
        if column.kind == "datetime":
            payload[column.name] = ensure_utc(datetime.fromisoformat(value))
        elif column.kind == "date":
            payload[column.name] = date.fromisoformat(value)
    payload = context.rewriter.rewrite(payload)
    for key, ref_table in contributor.foreign_keys.items():
        value = raw.get(key)
        mapped = context.id_map.get(ref_table, {}).get(str(value)) if value is not None else None
        if (
            value is not None
            and mapped is None
            and table != "desktop_video_sets"
            and (contributor.strict_references or ref_table in context.id_map and key != "avatar_id")
        ):
            raise ValueError(f"Missing {ref_table} reference in {table}.{key}")
        payload[key] = mapped
    if contributor.prepare_payload is not None:
        contributor.prepare_payload(raw, payload, context)
    if table not in {"messages", "user_preferences"}:
        payload["user_id"] = context.user_id
    return payload


async def insert_rows(
    db: AsyncSession,
    table: str,
    raw_rows: list[dict[str, Any]],
    target_user_id: int,
    rewriter: UrlRewriter,
    id_map: IdMap,
    *,
    mode: BackupImportMode,
    import_batch_id: str,
    asset_owner_id: int,
) -> tuple[dict[str, int | str], int]:
    context = BackupContext(target_user_id, asset_owner_id, mode, import_batch_id, rewriter, id_map)
    return await BACKUP_REGISTRY.table(table).restore(
        db,
        raw_rows,
        context,
        lambda raw: _build_payload(table, raw, context),
    )


def read_table_rows(extract_root: Path, table: str) -> list[dict[str, Any]]:
    payload = json.loads((extract_root / "db" / f"{table}.json").read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid rows in {table}.json")
    if table == "user_preferences":
        if len(rows) > 1:
            raise ValueError("Backup contains multiple user preference rows")
        return rows
    if any("id" not in row for row in rows):
        raise ValueError(f"Missing row id in {table}.json")
    if len({str(row["id"]) for row in rows}) != len(rows):
        raise ValueError(f"Duplicate row id in {table}.json")
    return rows
