"""0001 结构的关系图恢复；预检和所选数据分别原子完成。"""

import asyncio
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from features.accounts.backup import create_validation_account
from features.assets.lifecycle import enqueue_asset_cleanup
from features.assets.references import collect_live_asset_paths
from features.conversation.backup import validate_main_conversation_merge
from features.media.backup import restore_current_voices
from features.media.contracts import RemovedVideoTask
from features.topics.backup import validate_batch_merges
from features.visuals.backup import advance_playback_versions, playback_versions, visual_video_tasks_for_removal
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.assets.references import asset_paths
from infrastructure.async_io import run_blocking, settle_task
from infrastructure.backup import BACKUP_REGISTRY, BackupContext, BackupImportMode
from infrastructure.logger import get_logger
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from .file_packing import UrlRewriter, plan_asset_mapping, referenced_backup_files, restore_files, validate_row_files
from .serializers import CONVERSATION_TABLES, TABLES, VISUALS_TABLES, insert_rows, read_table_rows

logger = get_logger(__name__)
_SHARED_TABLES = frozenset({"assets", "jobs", "job_requests", "job_assets", "life_action_admissions"})


@dataclass(frozen=True)
class BackupImportFailure:
    section: str
    count: int
    reason: str


@dataclass(frozen=True)
class BackupReadResult:
    rows: dict[str, list[dict[str, Any]]]
    failures: tuple[BackupImportFailure, ...]


@dataclass(frozen=True)
class BackupRestoreResult:
    imported: dict[str, int]
    restored_files: int
    failures: tuple[BackupImportFailure, ...]
    rewriter: UrlRewriter
    removed_visual_tasks: tuple[RemovedVideoTask, ...]


def load_backup_rows(
    extract_root: Path,
    manifest_tables: list[str],
    row_counts: dict[str, int],
    *,
    selected_tables: frozenset[str] | None = None,
) -> BackupReadResult:
    wanted = set(TABLES) if selected_tables is None else set(selected_tables)
    failures = []
    rows: dict[str, list[dict[str, Any]]] = {}
    if set(manifest_tables) != set(TABLES):
        raise ValueError("Backup tables must match the complete 0001 schema")
    for table in TABLES:
        if table not in wanted:
            continue
        try:
            records = read_table_rows(extract_root, table)
            if len(records) != row_counts[table]:
                raise ValueError("Backup row count does not match manifest")
            rows[table] = records
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(BackupImportFailure(table, row_counts.get(table, 0), str(exc)))
    if failures:
        return BackupReadResult({}, tuple(failures))
    if wanted == set(TABLES):
        BACKUP_REGISTRY.validate_job_graph(rows)
    if "jobs" in rows and wanted != set(TABLES):
        job_ids = {
            str(record["id"])
            for name, records in rows.items()
            if BACKUP_REGISTRY.table(name).inherited_identity == "jobs"
            for record in records
        }
        rows["jobs"] = [record for record in rows["jobs"] if str(record["id"]) in job_ids]
        for name in ("job_requests", "job_assets"):
            if name in rows:
                rows[name] = [record for record in rows[name] if str(record["job_id"]) in job_ids]
    BACKUP_REGISTRY.validate_job_graph(rows)
    if rows.get("assets"):
        ids = {
            str(record[column])
            for name, records in rows.items()
            if name != "assets"
            for column, parent in BACKUP_REGISTRY.table(name).references.items()
            if parent == "assets"
            for record in records
            if record.get(column) is not None
        }
        parsed = parse_companion_asset_path(rows["assets"][0]["path"])
        referenced = (
            asset_paths({name: values for name, values in rows.items() if name != "assets"}, parsed[0])
            if parsed
            else set()
        )
        rows["assets"] = [
            record for record in rows["assets"] if str(record["id"]) in ids or record["path"] in referenced
        ]
    return BackupReadResult(rows, ())


async def _finish_relations(db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext) -> None:
    patches: dict[str, dict[str, dict[str, Any]]] = {}
    for table, original_id, column, target, target_id in context.rewriter.pending_references:
        if (table, original_id) not in context.rewriter.inserted_rows:
            continue
        mapped = context.id_map.get(target, {}).get(target_id)
        contributor = BACKUP_REGISTRY.table(table)
        if mapped is None and contributor.strict_references and target in rows:
            raise ValueError(f"Missing {target} reference in {table}.{column}")
        row_id = context.id_map[table][original_id]
        patches.setdefault(table, {}).setdefault(original_id, {"id": row_id})[column] = mapped
    for table, records in patches.items():
        await BACKUP_REGISTRY.table(table).patch_rows(db, list(records.values()))
    for table in TABLES:
        if table in rows and (callback := BACKUP_REGISTRY.table(table).restore_context) is not None:
            await callback(db, rows, context)
    await db.flush()
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def _insert_graph(
    db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext
) -> dict[str, int]:
    imported: dict[str, int] = {}
    for table in TABLES:
        if table not in rows:
            continue
        mode = "merge" if table in _SHARED_TABLES else context.mode
        context.id_map[table], imported[table] = await insert_rows(
            db,
            table,
            rows[table],
            context.user_id,
            context.rewriter,
            context.id_map,
            mode=mode,
            import_batch_id=context.import_batch_id,
            asset_owner_id=context.asset_owner_id,
        )
        if table == "conversations":
            context.rewriter.conversations = context.id_map[table]
    await _finish_relations(db, rows, context)
    return imported


async def _preflight(
    db: AsyncSession,
    rows: dict[str, list[dict[str, Any]]],
    extract_root: Path,
    source_user_id: int,
    target_user_id: int,
    mode: BackupImportMode,
    import_batch_id: str,
) -> tuple[BackupImportFailure, ...]:
    rewriter = UrlRewriter(
        plan_asset_mapping(rows, source_user_id, target_user_id),
        source_user_id=source_user_id,
        target_user_id=target_user_id,
        extract_root=extract_root,
        conversation_ids=frozenset(str(row["id"]) for row in rows.get("conversations", [])),
    )
    try:
        BACKUP_REGISTRY.validate_job_graph(rows)
        if mode == "merge":
            await validate_main_conversation_merge(db, target_user_id, rows.get("conversations", []))
            await validate_batch_merges(db, target_user_id, rows)
        for table, records in rows.items():
            await run_blocking(validate_row_files, table, records, rewriter)
        validation_user_id = await create_validation_account(db)
        await _insert_graph(
            db, rows, BackupContext(validation_user_id, target_user_id, mode, import_batch_id, rewriter, {})
        )
        return ()
    except (KeyError, TypeError, ValueError, StatementError) as exc:
        logger.warning("backup graph failed 0001 preflight", extra={"error_type": type(exc).__name__})
        if isinstance(exc, ValidationError):
            reason = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in exc.errors(include_input=False, include_url=False)
            )[:500]
        else:
            reason = (
                str(exc)[:500] if isinstance(exc, (KeyError, TypeError, ValueError)) else "数据不符合当前数据库约束。"
            )
        return tuple(BackupImportFailure(table, len(records), reason) for table, records in rows.items())
    finally:
        await db.rollback()


async def _clear_selected(db: AsyncSession, user_id: int, rows: dict[str, list[dict[str, Any]]]) -> None:
    selected = set(rows) - _SHARED_TABLES - {"user_preferences"}
    await BACKUP_REGISTRY.detach_retained_job_targets(db, user_id, selected)
    for child_name in BACKUP_REGISTRY.names:
        if child_name in selected:
            continue
        child = BACKUP_REGISTRY.table(child_name)
        for column, parent in child.references.items():
            if parent in selected and await child.has_rows(db, user_id, column):
                raise ValueError(f"保留的 {child_name} 仍引用 {parent}，请同时选择相关类别。")
    for table in reversed(TABLES):
        if table in selected:
            await BACKUP_REGISTRY.table(table).clear(db, user_id, preserve_unfinished=True)


async def _copy_backup_files(
    extract_root: Path,
    source_user_id: int,
    target_user_id: int,
    rewriter: UrlRewriter,
    *,
    conversations: dict[str, int | str],
    referenced_files: frozenset[str],
    include_conversation_files: bool,
) -> int:
    """线程复制备份文件；取消时通知线程停止并等其退出，调用方回滚时 rewriter.created 才完整。"""
    stop = threading.Event()
    task = asyncio.create_task(
        asyncio.to_thread(
            restore_files,
            extract_root,
            source_user_id,
            target_user_id,
            rewriter,
            stop,
            conversations=conversations,
            referenced_files=referenced_files,
            include_conversation_files=include_conversation_files,
        ),
        name="backup.restore.copy-files",
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # to_thread 包装任务不能取消：线程仍可能写盘，必须持有它直到实际退出再交还维护边界。
        stop.set()
        try:
            await settle_task(task)
        except InterruptedError:
            pass
        except Exception:
            logger.warning("backup file copy failed during cancellation cleanup", exc_info=True)
        raise


async def restore_backup_rows(
    db: AsyncSession,
    extract_root: Path,
    source_user_id: int,
    target_user_id: int,
    rows: dict[str, list[dict[str, Any]]],
    *,
    mode: BackupImportMode,
) -> BackupRestoreResult:
    import_batch_id = uuid.uuid4().hex
    failures = await _preflight(db, rows, extract_root, source_user_id, target_user_id, mode, import_batch_id)
    rewriter = UrlRewriter(
        plan_asset_mapping(rows, source_user_id, target_user_id),
        source_user_id=source_user_id,
        target_user_id=target_user_id,
        extract_root=extract_root,
        conversation_ids=frozenset(str(row["id"]) for row in rows.get("conversations", [])),
    )
    if failures:
        return BackupRestoreResult({}, 0, failures, rewriter, ())
    previous_versions = await playback_versions(db, target_user_id)
    removed_tasks: tuple[RemovedVideoTask, ...] = ()
    try:
        if mode == "overwrite":
            if VISUALS_TABLES.issubset(rows):
                removed_tasks = await visual_video_tasks_for_removal(db, target_user_id)
            await enqueue_asset_cleanup(db, target_user_id, await collect_live_asset_paths(db, target_user_id))
            await _clear_selected(db, target_user_id, rows)
        context = BackupContext(target_user_id, target_user_id, mode, import_batch_id, rewriter, {})
        imported = await _insert_graph(db, rows, context)
        if {"custom_voices", "user_settings"} <= imported.keys():
            await restore_current_voices(
                db, target_user_id, rows["custom_voices"], context.id_map["custom_voices"], mode=mode
            )
        skipped = await _copy_backup_files(
            extract_root,
            source_user_id,
            target_user_id,
            rewriter,
            conversations=context.id_map.get("conversations", {}),
            referenced_files=await run_blocking(referenced_backup_files, extract_root, rows),
            include_conversation_files=bool(CONVERSATION_TABLES & rows.keys()),
        )
        if skipped:
            raise ValueError("附件缺少可用的目标会话")
        if VISUALS_TABLES.issubset(imported):
            await advance_playback_versions(db, target_user_id, previous_versions)
        await db.flush()
        return BackupRestoreResult(imported, len(rewriter.created), (), rewriter, removed_tasks)
    except BaseException:
        rewriter.rollback()
        raise
