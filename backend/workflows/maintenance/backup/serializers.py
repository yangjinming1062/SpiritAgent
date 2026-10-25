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
        "message_assets",
        "message_voice_refs",
        "companion_topic_batches",
        "companion_topics",
        "companion_topic_feedback",
        "companion_topic_usages",
        "video_gen_jobs",
    }
)
VISUALS_TABLES = frozenset(
    {
        "visual_items",
        "visual_versions",
        "visual_version_assets",
        "visual_jobs",
        "visual_presentations",
        "visual_playbacks",
    }
)
ACTION_LIBRARY_TABLES = VISUALS_TABLES | {
    "companion_outfits",
    "action_definitions",
    "companion_scenes",
    "scene_jobs",
    "appearance_assets",
    "avatar_assets",
    "personas",
    "character_analysis_jobs",
    "companion_fullbody_candidates",
}
POST_TABLES = frozenset(
    {"companion_entries", "entry_links", "entry_assets", "entry_comments", "post_publications", "video_gen_jobs"}
)
BACKUP_SECTIONS: dict[str, tuple[str, ...]] = {
    "identity": (
        "personas",
        "avatar_assets",
        "companion_outfits",
        "companion_fullbody_candidates",
        "character_analysis_jobs",
        "appearance_assets",
    ),
    "conversations": tuple(sorted(CONVERSATION_TABLES)),
    "memories": ("memories", "memory_evidence", "memory_revisions", "memory_fingerprints"),
    "entries": tuple(sorted(POST_TABLES)),
    "wardrobe": tuple(sorted(ACTION_LIBRARY_TABLES)),
    "scenes": tuple(sorted(ACTION_LIBRARY_TABLES)),
    "visuals": tuple(sorted(ACTION_LIBRARY_TABLES)),
    "automation": (
        "cron_jobs",
        "companion_intents",
        "life_pursuits",
        "life_steps",
        "life_events",
        "life_states",
        "pursuit_evidence",
    ),
    "settings": ("user_model_configs", "custom_voices", "user_settings", "user_preferences"),
}
BACKUP_SECTION_IDS = tuple(BACKUP_SECTIONS)


def tables_for_sections(sections: Sequence[str] | None) -> tuple[frozenset[str], list[str]]:
    """解析导入分组为表集合与回显分组 ID；None 或 all 表示全部，空列表或未知 ID 抛 ValueError。"""
    if sections is None:
        return frozenset(TABLES), list(BACKUP_SECTION_IDS)
    normalized = [item.strip() for item in sections if item and item.strip()]
    if not normalized:
        raise ValueError("恢复范围不能为空")
    if "all" in normalized:
        if len(normalized) > 1:
            raise ValueError("恢复范围 all 不能与其他分组同时指定")
        return frozenset(TABLES), list(BACKUP_SECTION_IDS)
    unknown = [item for item in normalized if item not in BACKUP_SECTIONS]
    if unknown:
        raise ValueError(f"未知的恢复范围：{', '.join(unknown)}")
    tables: set[str] = set()
    for item in normalized:
        tables.update(BACKUP_SECTIONS[item])
    # 共享资产和任务记录采用合并恢复，不清除其他类别正在使用的记录。
    tables.update(name for name in ("assets", "jobs", "job_requests", "job_assets") if name in TABLES)
    changed = True
    while changed:
        before = set(tables)
        for table in tuple(tables):
            if table in BACKUP_REGISTRY.names:
                contributor = BACKUP_REGISTRY.table(table)
                tables.update(
                    target
                    for column, target in contributor.references.items()
                    if target in TABLES and not contributor.column_nullable(column)
                )
        changed = before != tables
    return frozenset(tables), normalized


@cache
def _columns(table: str) -> tuple[str, ...]:
    return tuple(column.name for column in BACKUP_REGISTRY.table(table).columns)


def _build_payload(table: str, raw: dict[str, Any], context: BackupContext) -> dict[str, Any]:
    contributor = BACKUP_REGISTRY.table(table)
    columns = _columns(table)
    if unknown := raw.keys() - set(columns):
        raise ValueError(f"Unknown columns in {table}: {', '.join(sorted(unknown))}")
    if missing := set(columns) - raw.keys():
        raise ValueError(f"Missing columns in {table}: {', '.join(sorted(missing))}")
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
    for key, ref_table in contributor.references.items():
        if key == contributor.identity_name:
            continue
        value = raw.get(key)
        if value is None:
            payload[key] = None
            continue
        mapped = context.id_map.get(ref_table, {}).get(str(value))
        if mapped is None:
            if contributor.column_nullable(key):
                context.rewriter.pending_references.append((table, str(raw["id"]), key, ref_table, str(value)))
            else:
                raise ValueError(f"Missing {ref_table} reference in {table}.{key}")
        payload[key] = mapped
    if contributor.prepare_payload is not None:
        contributor.prepare_payload(raw, payload, context)
    if contributor.ownership_column is not None:
        payload[contributor.ownership_column] = context.user_id
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
    if not isinstance(payload, dict) or set(payload) != {"table", "rows"} or payload["table"] != table:
        raise ValueError(f"Invalid table envelope in {table}.json")
    rows = payload["rows"]
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
