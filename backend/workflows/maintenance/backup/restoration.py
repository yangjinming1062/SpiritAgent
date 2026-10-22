import asyncio
import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from features.accounts.backup import create_validation_account
from features.actions.backup_assets import restore_action_catalogs, validate_action_files
from features.assets.lifecycle import enqueue_asset_cleanup
from features.assets.references import collect_live_asset_paths
from features.companion.backup import advance_scene_versions, cron_source_keys, scene_versions
from features.configuration.settings.timezone import resolve_user_timezone
from features.conversation.backup import validate_main_conversation_merge
from features.desktop_life.backup import advance_playback_versions, desktop_video_tasks_for_removal, playback_versions
from features.desktop_life.backup_assets import validate_desktop_files
from features.media.backup import restore_current_voices
from features.media.contracts import RemovedActionVideoTask
from features.media.voice_queries import custom_voice_tokens
from features.topics.backup import validate_batch_merges
from infrastructure.async_io import run_blocking, settle_task
from infrastructure.backup import BACKUP_REGISTRY, BackupContext, BackupImportMode
from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.maintenance.backup.asset_layout import freeze_asset_directory, plan_asset_layout
from workflows.maintenance.backup.file_packing import (
    UrlRewriter,
    planned_asset_mapping,
    referenced_backup_files,
    restore_files,
    validate_row_files,
)
from workflows.maintenance.backup.serializers import (
    ACTION_LIBRARY_TABLES,
    ACTION_TABLES,
    ATOMIC_SECTION_GROUPS,
    CONVERSATION_TABLES,
    DESKTOP_TABLES,
    IDENTITY_BLOCKED_REASON,
    IDENTITY_DEPENDENT_REASON,
    IDENTITY_GROUP,
    IDENTITY_INCOMPLETE_REASON,
    IDENTITY_TABLES,
    POST_TABLES,
    TABLES,
    insert_rows,
    read_table_rows,
)

logger = get_logger(__name__)

# 这些表的 SQL 参数含供应商密钥，数据库错误的堆栈不写入日志。
_SECRET_TABLES = frozenset({"user_model_configs"})
BACKUP_RESTORE_ORDER: tuple[str, ...] = (
    "user_model_configs",
    "custom_voices",
    "conversations",
    "companion_topic_batches",
    "companion_topics",
    "companion_topic_feedback",
    "messages",
    "companion_topic_usages",
    *(table for table in TABLES if table not in CONVERSATION_TABLES | {"user_model_configs", "custom_voices"}),
)
# conversations 先写入以就绪 rewriter 的会话映射，其余表保持 BACKUP_RESTORE_ORDER 原序。
RESTORE_SEQUENCE: tuple[str, ...] = ("conversations",) + tuple(
    table for table in BACKUP_RESTORE_ORDER if table != "conversations"
)
# 父类覆盖会改变仍被保留的子类引用，子类未随本次恢复清理时须保留父类；None 表示引用嵌在 JSON / 数组等非关系列中，只要存在保留行就按可能有关联处理。
OVERWRITE_DEPENDENT_REFERENCES: dict[str, tuple[tuple[str, str | None], ...]] = {
    "avatar_assets": (
        ("companion_character_cards", "avatar_id"),
        ("companion_action_packs", "avatar_id"),
    ),
    "companion_outfits": (("companion_action_packs", "outfit_id"), ("action_definitions", "outfit_id")),
    "action_definitions": (
        ("companion_actions", "definition_id"),
        ("desktop_video_actions", "definition_id"),
        ("action_sync_targets", "definition_id"),
    ),
    "companion_action_packs": (("companion_actions", "pack_id"), ("action_sync_targets", None)),
    "cron_jobs": (("companion_intents", "source_key"),),
    "conversations": (
        ("cron_jobs", "conversation_id"),
        ("memories", None),
    ),
    "companion_scenes": (("personas", "active_scene_id"),),
    "desktop_video_sets": (
        ("desktop_video_actions", "set_id"),
        ("desktop_video_states", "current_set_id"),
        ("action_sync_targets", None),
    ),
    "desktop_video_actions": (
        ("desktop_video_states", "selected_action_id"),
        ("desktop_video_states", "loop_action_id"),
    ),
    "companion_posts": (("companion_diary_entries", None),),
}


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
    removed_desktop_tasks: tuple[RemovedActionVideoTask, ...]


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, (KeyError, TypeError, ValueError)):
        return (str(exc).strip() or type(exc).__name__)[:500]
    return "数据不符合当前版本约束。"


def load_backup_rows(
    extract_root: Path,
    manifest_tables: list[str],
    row_counts: dict[str, int],
    *,
    selected_tables: frozenset[str] | None = None,
) -> BackupReadResult:
    wanted = frozenset(TABLES) if selected_tables is None else selected_tables
    manifest_set = set(manifest_tables)
    failures = [
        BackupImportFailure(
            section=table,
            count=row_counts[table],
            reason="当前版本不支持此数据类别。",
        )
        for table in manifest_tables
        if table not in TABLES
    ]
    # 所选类别缺表时报告失败。
    for table in sorted(wanted - manifest_set):
        if table not in TABLES:
            continue
        failures.append(
            BackupImportFailure(
                section=table,
                count=0,
                reason="备份未包含此数据类别。",
            ),
        )
    incomplete_conversation_tables = set(manifest_tables) & CONVERSATION_TABLES & wanted
    incomplete_conversation_backup = (
        bool(incomplete_conversation_tables) and incomplete_conversation_tables != CONVERSATION_TABLES
    )
    if incomplete_conversation_backup:
        failures.extend(
            BackupImportFailure(
                section=table,
                count=row_counts[table],
                reason="会话、消息与资讯来源必须同时存在，无法单独恢复。",
            )
            for table in manifest_tables
            if table in incomplete_conversation_tables
        )

    rows: dict[str, list[dict[str, Any]]] = {}
    for table in manifest_tables:
        if (
            table not in wanted
            or table not in TABLES
            or incomplete_conversation_backup
            and table in incomplete_conversation_tables
        ):
            continue
        try:
            records = read_table_rows(extract_root, table)
            if len(records) != row_counts[table]:
                raise ValueError("Backup row count does not match manifest")
            rows[table] = records
        except Exception as exc:
            logger.warning(
                "backup table could not be read",
                extra={"table": table},
                exc_info=not isinstance(exc, (KeyError, TypeError, ValueError)),
            )
            failures.append(
                BackupImportFailure(
                    section=table,
                    count=row_counts[table],
                    reason=_failure_reason(exc),
                ),
            )
    # 基础身份等成组类别：缺任一表则整组不恢复，避免落成半个身份。
    for group in ATOMIC_SECTION_GROUPS:
        if not group & wanted:
            continue
        present = group & set(rows)
        if present and present != group:
            reason = (
                IDENTITY_INCOMPLETE_REASON
                if group == IDENTITY_TABLES
                else "会话、消息与资讯来源必须同时恢复。"
                if group == CONVERSATION_TABLES
                else "动态与评论必须同时恢复。"
                if group == POST_TABLES
                else "着装、共享动作和两种模式的动作版本必须同时恢复。"
            )
            for table in sorted(present):
                rows.pop(table)
                failures.append(
                    BackupImportFailure(
                        section=table,
                        count=row_counts.get(table, 0),
                        reason=reason,
                    ),
                )
    return BackupReadResult(rows=rows, failures=tuple(failures))


async def _restore_table(
    db: AsyncSession,
    table: str,
    records: list[dict[str, Any]],
    rows: dict[str, list[dict[str, Any]]],
    user_id: int,
    rewriter: UrlRewriter,
    id_map: dict[str, dict[str, int | str]],
    *,
    mode: BackupImportMode,
    import_batch_id: str,
    asset_owner_id: int,
) -> tuple[dict[str, int | str], int]:
    new_map, inserted = await insert_rows(
        db,
        table,
        records,
        user_id,
        rewriter,
        id_map,
        mode=mode,
        import_batch_id=import_batch_id,
        asset_owner_id=asset_owner_id,
    )
    staged_id_map = {**id_map, table: new_map}
    if table == "conversations":
        rewriter.conversations = new_map
    contributor = BACKUP_REGISTRY.table(table)
    if contributor.restore_context is not None:
        context = BackupContext(user_id, asset_owner_id, mode, import_batch_id, rewriter, staged_id_map)
        await contributor.restore_context(db, rows, context)
    return new_map, inserted


def _validate_voice_dependencies(
    table: str,
    rows: dict[str, list[dict[str, Any]]],
    successful: set[str],
    rewriter: UrlRewriter,
) -> None:
    tokens: set[str] = set()
    if table == "conversations":
        tokens = custom_voice_tokens([row.get("reply_json") for row in rows.get("messages", [])])
    elif table == "user_settings":
        for row in rows[table]:
            if row.get("setting_key") == "companion.voice_id":
                tokens |= custom_voice_tokens(json.loads(row["setting_value"]))
    if not tokens:
        return
    profiles = {row["token"]: row for row in rows.get("custom_voices", []) if row.get("version", 0) > 0}
    if "custom_voices" not in successful or not tokens <= profiles.keys():
        raise ValueError("引用的声音方案无法恢复，此类别已保留原有数据。")
    for token in tokens:
        profile = profiles[token]
        if profile["kind"] == "clone":
            rewriter.require_file(profile.get("reference_path") or "")


async def _preflight_tables(
    db: AsyncSession,
    rows: dict[str, list[dict[str, Any]]],
    *,
    extract_root: Path,
    source_user_id: int,
    target_user_id: int,
    mode: BackupImportMode,
    import_batch_id: str,
) -> tuple[set[str], tuple[BackupImportFailure, ...]]:
    successful: set[str] = set()
    failures: list[BackupImportFailure] = []
    conversation_merge_conflict: str | None = None
    if mode == "merge":
        try:
            await validate_main_conversation_merge(db, target_user_id, rows.get("conversations", []))
            await validate_batch_merges(db, target_user_id, rows)
        except ValueError as exc:
            conversation_merge_conflict = str(exc)
    id_map: dict[str, dict[str, int | str]] = {}
    rewriter = UrlRewriter(
        await run_blocking(planned_asset_mapping, extract_root, source_user_id, target_user_id),
        source_user_id=source_user_id,
        target_user_id=target_user_id,
        extract_root=extract_root,
        conversation_ids=frozenset(str(row["id"]) for row in rows.get("conversations", [])),
    )
    # 未激活且 token 不外泄：该用户只承载预检行，结束即回滚。
    try:
        validation_user_id = await create_validation_account(db)
        for table in BACKUP_RESTORE_ORDER:
            if table not in rows:
                continue
            if table in ACTION_LIBRARY_TABLES - {"companion_outfits"}:
                continue
            if table in CONVERSATION_TABLES - {"conversations"} and "conversations" in rows:
                continue
            if table == "companion_post_comments" and "companion_posts" in rows:
                continue
            # 身份三表与动作两表各自成组预检，避免只写通一半。
            if table in {"companion_character_cards", "personas"} and "avatar_assets" in rows:
                continue
            if table == "conversations":
                group = tuple(member for member in BACKUP_RESTORE_ORDER if member in CONVERSATION_TABLES)
            elif table == "avatar_assets" and IDENTITY_TABLES & set(rows):
                group = tuple(member for member in IDENTITY_GROUP if member in rows)
            elif table == "companion_outfits":
                group = tuple(member for member in BACKUP_RESTORE_ORDER if member in ACTION_LIBRARY_TABLES)
            elif table == "companion_posts":
                group = ("companion_posts", "companion_post_comments")
            else:
                group = (table,)
            available_rows = {name: rows[name] for name in successful | set(group) if name in rows}
            staged_map = dict(id_map)
            try:
                await run_blocking(_validate_voice_dependencies, table, rows, successful, rewriter)
                if table == "conversations" and conversation_merge_conflict is not None:
                    raise ValueError(conversation_merge_conflict)
                async with db.begin_nested():
                    for member in group:
                        await run_blocking(validate_row_files, member, rows[member], rewriter)
                        staged_map[member], _ = await _restore_table(
                            db,
                            member,
                            rows[member],
                            available_rows,
                            validation_user_id,
                            rewriter,
                            staged_map,
                            mode=mode,
                            import_batch_id=import_batch_id,
                            # 预检行属于校验用户，包内资产仍按恢复后的真实归属判断。
                            asset_owner_id=target_user_id,
                        )
                    if table == "companion_outfits":
                        await run_blocking(
                            validate_action_files,
                            available_rows,
                            extract_root,
                            source_user_id,
                            target_user_id,
                        )
                        await restore_action_catalogs(
                            db,
                            available_rows,
                            staged_map,
                            validation_user_id,
                            UrlRewriter({}),
                            write_files=False,
                        )
                        await run_blocking(
                            validate_desktop_files,
                            available_rows,
                            extract_root,
                            source_user_id,
                            target_user_id,
                        )
            except (KeyError, StatementError, TypeError, ValueError) as exc:
                # 对外原因不含数据库细节，诊断保留在日志里。
                logger.warning(
                    "backup table failed compatibility preflight",
                    extra={"table": table, "error_type": type(exc).__name__},
                    exc_info=not (table in _SECRET_TABLES and isinstance(exc, StatementError)),
                )
                failures.extend(
                    BackupImportFailure(member, len(rows[member]), _failure_reason(exc))
                    for member in group
                    if member in rows
                )
                continue
            id_map = staged_map
            successful.update(group)
        if CONVERSATION_TABLES.issubset(rows) and not CONVERSATION_TABLES.issubset(successful):
            for table in BACKUP_RESTORE_ORDER:
                if table not in CONVERSATION_TABLES or table not in successful:
                    continue
                successful.remove(table)
                failures.append(
                    BackupImportFailure(
                        section=table,
                        count=len(rows[table]),
                        reason="配套的会话或消息数据不兼容，无法单独恢复。",
                    ),
                )
        if IDENTITY_TABLES & set(rows) and not IDENTITY_TABLES.issubset(successful):
            for table in sorted(IDENTITY_TABLES & successful):
                successful.remove(table)
                failures.append(
                    BackupImportFailure(
                        section=table,
                        count=len(rows[table]),
                        reason=IDENTITY_INCOMPLETE_REASON,
                    ),
                )
    finally:
        await db.rollback()
    return successful, tuple(failures)


async def _has_retained_dependent(
    db: AsyncSession,
    table: str,
    target_user_id: int,
    compatible_rows: dict[str, list[dict[str, Any]]],
) -> bool:
    for dependent_table, reference_column in OVERWRITE_DEPENDENT_REFERENCES.get(table, ()):
        if dependent_table in compatible_rows:
            continue
        contributor = BACKUP_REGISTRY.table(dependent_table)
        reference_values = None
        if table == "cron_jobs":
            reference_values = cron_source_keys(await BACKUP_REGISTRY.table("cron_jobs").ids(db, target_user_id))
        if await contributor.has_rows(db, target_user_id, reference_column, reference_values):
            return True
    return False


async def _delete_user_rows(db: AsyncSession, table: str, user_id: int) -> None:
    await BACKUP_REGISTRY.table(table).clear(db, user_id)


async def _clear_compatible_rows(
    db: AsyncSession,
    target_user_id: int,
    compatible_rows: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], tuple[BackupImportFailure, ...]]:
    remaining = dict(compatible_rows)
    failures: list[BackupImportFailure] = []

    # 动态与评论同组保护：保留的日记仍引用动态时，在任何清理前阻止两表覆盖。
    if "companion_posts" in remaining and await _has_retained_dependent(
        db,
        "companion_posts",
        target_user_id,
        remaining,
    ):
        for table in ("companion_posts", "companion_post_comments"):
            records = remaining.pop(table, None)
            if records is not None:
                failures.append(BackupImportFailure(table, len(records), "目标已有日记仍关联动态，无法单独覆盖动态。"))
    if ACTION_LIBRARY_TABLES.issubset(remaining):
        library_failure = None
        if "avatar_assets" in remaining and await _has_retained_dependent(
            db,
            "avatar_assets",
            target_user_id,
            remaining,
        ):
            library_failure = "动作版本引用的基础身份无法安全覆盖。"
        if library_failure is None:
            try:
                async with db.begin_nested():
                    for table in reversed(TABLES):
                        if table in ACTION_LIBRARY_TABLES:
                            await _delete_user_rows(db, table, target_user_id)
            except IntegrityError:
                logger.warning(
                    "shared action group could not be cleared without affecting retained data",
                    extra={"target_user_id": target_user_id},
                    exc_info=True,
                )
                library_failure = "共享动作仍被其他内容引用，无法成组覆盖。"
        if library_failure is not None:
            for table in sorted(ACTION_LIBRARY_TABLES):
                failures.append(BackupImportFailure(table, len(remaining.pop(table)), library_failure))

    def _drop_identity(reason: str) -> None:
        for member in sorted(IDENTITY_TABLES & set(remaining)):
            remaining.pop(member)
            failures.append(
                BackupImportFailure(
                    section=member,
                    count=len(compatible_rows.get(member, ())),
                    reason=reason,
                ),
            )

    # 身份三表一起判断、一起清理，避免先删人设后才发现头像被场景引用。
    if IDENTITY_TABLES & set(remaining):
        if await _has_retained_dependent(db, "avatar_assets", target_user_id, remaining):
            _drop_identity(IDENTITY_BLOCKED_REASON)
        else:
            try:
                async with db.begin_nested():
                    for member in ("companion_character_cards", "personas", "avatar_assets"):
                        if member in remaining:
                            await _delete_user_rows(db, member, target_user_id)
            except IntegrityError:
                logger.warning(
                    "backup identity tables could not be cleared without affecting retained data",
                    extra={"target_user_id": target_user_id},
                    exc_info=True,
                )
                _drop_identity(IDENTITY_BLOCKED_REASON)

    # 身份未能写入时先摘掉依赖头像映射的动作类别，避免在清理阶段误删。
    if IDENTITY_TABLES & set(compatible_rows) and not set(remaining) >= IDENTITY_TABLES:
        for table in sorted(ACTION_LIBRARY_TABLES):
            if table not in remaining:
                continue
            remaining.pop(table)
            failures.append(
                BackupImportFailure(
                    section=table,
                    count=len(compatible_rows.get(table, ())),
                    reason=IDENTITY_DEPENDENT_REASON,
                ),
            )

    for table in reversed(TABLES):
        if table in ACTION_LIBRARY_TABLES | IDENTITY_TABLES | CONVERSATION_TABLES:
            continue
        # user_preferences 就地更新用户行；messages 随 conversations 级联删除。
        if table not in remaining or table in {"user_preferences", "messages"}:
            continue
        if await _has_retained_dependent(db, table, target_user_id, remaining):
            remaining.pop(table)
            failures.append(
                BackupImportFailure(
                    section=table,
                    count=len(compatible_rows[table]),
                    reason="目标现有的关联数据未被本次恢复，无法安全覆盖此类别。",
                ),
            )
            continue
        try:
            async with db.begin_nested():
                await _delete_user_rows(db, table, target_user_id)
        except IntegrityError:
            logger.warning(
                "backup table could not be cleared without affecting retained data",
                extra={"table": table, "target_user_id": target_user_id},
                exc_info=True,
            )
            remaining.pop(table)
            failures.append(
                BackupImportFailure(
                    section=table,
                    count=len(compatible_rows[table]),
                    reason="目标现有数据仍被其他内容引用，无法安全覆盖此类别。",
                ),
            )
    if CONVERSATION_TABLES.issubset(remaining):
        reason = None
        if await _has_retained_dependent(db, "conversations", target_user_id, remaining):
            reason = "目标仍有数据关联会话，已保留会话、消息与资讯来源。"
        else:
            try:
                async with db.begin_nested():
                    for member in reversed(BACKUP_RESTORE_ORDER):
                        if member in CONVERSATION_TABLES and member != "messages":
                            await _delete_user_rows(db, member, target_user_id)
            except IntegrityError:
                logger.warning(
                    "backup conversation group could not be cleared",
                    extra={"target_user_id": target_user_id},
                    exc_info=True,
                )
                reason = "会话关联数据无法安全覆盖，已保留会话、消息与资讯来源。"
        if reason is not None:
            for member in sorted(CONVERSATION_TABLES):
                failures.append(BackupImportFailure(member, len(remaining.pop(member)), reason))
    # 覆盖清理后仍须成组。
    for group in (IDENTITY_TABLES, ACTION_LIBRARY_TABLES):
        present = group & set(remaining)
        if present and present != group:
            reason = (
                IDENTITY_INCOMPLETE_REASON
                if group == IDENTITY_TABLES
                else "着装、共享动作和两种模式的动作版本必须同时恢复。"
            )
            for table in sorted(present):
                remaining.pop(table)
                failures.append(
                    BackupImportFailure(
                        section=table,
                        count=len(compatible_rows.get(table, ())),
                        reason=reason,
                    ),
                )
    return remaining, tuple(failures)


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
    conversation_files_in_scope = bool(CONVERSATION_TABLES & set(rows))
    successful_tables, failures = await _preflight_tables(
        db,
        rows,
        extract_root=extract_root,
        source_user_id=source_user_id,
        target_user_id=target_user_id,
        mode=mode,
        import_batch_id=import_batch_id,
    )
    compatible_rows = {table: records for table, records in rows.items() if table in successful_tables}
    rewriter = UrlRewriter(
        {},
        source_user_id=source_user_id,
        target_user_id=target_user_id,
        extract_root=extract_root,
        conversation_ids=frozenset(str(row["id"]) for row in compatible_rows.get("conversations", [])),
    )
    existing_scene_versions = await scene_versions(db, target_user_id)
    existing_desktop_versions = await playback_versions(db, target_user_id)
    removed_desktop_tasks: tuple[RemovedActionVideoTask, ...] = ()
    try:
        if mode == "overwrite":
            if DESKTOP_TABLES.issubset(compatible_rows):
                removed_desktop_tasks = await desktop_video_tasks_for_removal(db, target_user_id)
            await enqueue_asset_cleanup(db, target_user_id, await collect_live_asset_paths(db, target_user_id))
            compatible_rows, clear_failures = await _clear_compatible_rows(db, target_user_id, compatible_rows)
            if not DESKTOP_TABLES.issubset(compatible_rows):
                removed_desktop_tasks = ()
            failures = (*failures, *clear_failures)
        id_map: dict[str, dict[str, int | str]] = {}
        imported: dict[str, int] = {}
        for table in RESTORE_SEQUENCE:
            if table not in compatible_rows:
                continue
            id_map[table], imported[table] = await _restore_table(
                db,
                table,
                compatible_rows[table],
                compatible_rows,
                target_user_id,
                rewriter,
                id_map,
                mode=mode,
                import_batch_id=import_batch_id,
                asset_owner_id=target_user_id,
            )
        if {"custom_voices", "user_settings"} <= imported.keys():
            await restore_current_voices(
                db,
                target_user_id,
                compatible_rows["custom_voices"],
                id_map["custom_voices"],
                mode=mode,
            )
        layout = plan_asset_layout(
            compatible_rows,
            source_user_id,
            target_user_id,
            await resolve_user_timezone(db, target_user_id) or "UTC",
            id_map=id_map,
        )
        rewriter.set_asset_mapping(layout.mapping, layout.copies)
        skipped_conversation_files = await _copy_backup_files(
            extract_root,
            source_user_id,
            target_user_id,
            rewriter,
            conversations=id_map.get("conversations", {}),
            referenced_files=await run_blocking(referenced_backup_files, extract_root, compatible_rows),
            include_conversation_files=conversation_files_in_scope,
        )
        if skipped_conversation_files:
            failures = (
                *failures,
                BackupImportFailure(
                    section="asset_files",
                    count=skipped_conversation_files,
                    reason="对应会话未能恢复，附件缺少可用的目标会话。",
                ),
            )
        await run_blocking(rewriter.scan, compatible_rows)
        for table, original_id in rewriter.inserted_rows:
            mapping = layout.rows.get((table, original_id), layout.mapping)
            aliases = {
                old.replace(f"companion-assets/{source_user_id}/", f"companion-assets/{target_user_id}/", 1): new
                for old, new in mapping.items()
            }
            remap = UrlRewriter({**mapping, **aliases})

            def rewrite_column(column: str, value: Any) -> Any:
                return freeze_asset_directory(
                    table,
                    column,
                    remap.rewrite(value),
                    layout.directories[(table, original_id)],
                )

            await BACKUP_REGISTRY.table(table).rewrite_inserted(db, id_map[table][original_id], rewrite_column)
        await db.flush()
        if ACTION_TABLES.issubset(imported):
            await restore_action_catalogs(
                db,
                compatible_rows,
                id_map,
                target_user_id,
                rewriter,
                write_files=True,
            )
        await enqueue_asset_cleanup(
            db,
            target_user_id,
            {path.resolve().relative_to(Path(SETTINGS.data_dir).resolve()).as_posix() for path in rewriter.created},
        )
        if imported.get("personas") or imported.get("companion_scenes"):
            await advance_scene_versions(db, target_user_id, existing_scene_versions)
        if DESKTOP_TABLES.issubset(imported):
            await advance_playback_versions(db, target_user_id, existing_desktop_versions)
        if rewriter.missing_paths:
            failures = (
                *failures,
                BackupImportFailure(
                    "asset_files",
                    len(rewriter.missing_paths),
                    "部分历史媒体已清理，记录已恢复，媒体引用保持失效。",
                ),
            )
        return BackupRestoreResult(
            imported=imported,
            restored_files=len(rewriter.created),
            failures=failures,
            rewriter=rewriter,
            removed_desktop_tasks=removed_desktop_tasks,
        )
    except BaseException:
        rewriter.rollback()
        raise
