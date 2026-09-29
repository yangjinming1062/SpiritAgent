import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from components import get_logger
from modules.auth import User, generate_activation_token, hash_activation_token
from modules.companion import COMPANION_CRON_SOURCE_PREFIX, Persona
from modules.scheduler import CronJob
from modules.ws import emit_ws_event
from sqlalchemy import String, cast, delete, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from .action_assets import restore_action_catalogs, validate_action_files
from .file_packing import UrlRewriter, restore_files
from .serializers import (
    ACTION_TABLES,
    CONVERSATION_TABLES,
    TABLE_MODELS,
    TABLES,
    BackupImportMode,
    insert_rows,
    read_table_rows,
    restore_conversation_context,
    restore_memory_context,
)

logger = get_logger(__name__)

BACKUP_RESTORE_ORDER: tuple[str, ...] = (
    "conversations",
    "messages",
    *(table for table in TABLES if table not in CONVERSATION_TABLES),
)
# 父类覆盖会改变仍被保留的子类引用；子类没有随本次恢复清理时须保留父类。
# None 表示引用嵌在 JSON / 数组等非关系列中，只要存在保留行就按可能有关联处理。
OVERWRITE_DEPENDENT_REFERENCES: dict[str, tuple[tuple[str, str | None], ...]] = {
    "avatar_assets": (
        ("companion_character_cards", "avatar_id"),
        ("companion_scenes", None),
        ("companion_action_packs", "avatar_id"),
    ),
    "companion_outfits": (("companion_action_packs", "outfit_id"),),
    "companion_action_packs": (("companion_actions", "pack_id"),),
    "cron_jobs": (("companion_intents", "source_key"),),
    "conversations": (
        ("cron_jobs", "conversation_id"),
        ("companion_moments", "session_id"),
        ("memories", None),
    ),
    "companion_scenes": (("personas", "active_scene_id"),),
    "memories": (("companion_moments", "memory_id"), ("companion_diary_entries", None)),
    "companion_moments": (("companion_diary_entries", None),),
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


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, (KeyError, TypeError, ValueError)):
        return (str(exc).strip() or type(exc).__name__)[:500]
    return "数据不符合当前版本约束。"


def load_backup_rows(
    extract_root: Path,
    manifest_tables: list[str],
    row_counts: dict[str, int],
) -> BackupReadResult:
    failures = [
        BackupImportFailure(
            section=table,
            count=row_counts[table],
            reason="当前版本不支持此数据类别。",
        )
        for table in manifest_tables
        if table not in TABLES
    ]
    incomplete_conversation_tables = set(manifest_tables) & CONVERSATION_TABLES
    incomplete_conversation_backup = (
        bool(incomplete_conversation_tables) and incomplete_conversation_tables != CONVERSATION_TABLES
    )
    if incomplete_conversation_backup:
        failures.extend(
            BackupImportFailure(
                section=table,
                count=row_counts[table],
                reason="会话与消息必须同时存在，无法单独恢复。",
            )
            for table in manifest_tables
            if table in incomplete_conversation_tables
        )

    rows: dict[str, list[dict[str, Any]]] = {}
    for table in manifest_tables:
        if table not in TABLES or incomplete_conversation_backup and table in incomplete_conversation_tables:
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
    if CONVERSATION_TABLES.issubset(manifest_tables) and not CONVERSATION_TABLES.issubset(rows):
        for table in manifest_tables:
            if table not in CONVERSATION_TABLES or table not in rows:
                continue
            rows.pop(table)
            failures.append(
                BackupImportFailure(
                    section=table,
                    count=row_counts[table],
                    reason="配套的会话或消息数据无效，无法单独恢复。",
                ),
            )
    if set(manifest_tables) & ACTION_TABLES and not ACTION_TABLES.issubset(rows):
        for table in manifest_tables:
            if table in ACTION_TABLES and table in rows:
                rows.pop(table)
                failures.append(BackupImportFailure(table, row_counts[table], "视频包与动作必须同时恢复。"))
    elif not set(manifest_tables) & ACTION_TABLES:
        started = sum(bool(row.get("initial_video_started")) for row in rows.get("companion_outfits", []))
        if started:
            failures.append(
                BackupImportFailure(
                    "companion_action_packs",
                    started,
                    "备份未包含视频包和动作记录，视频文件无法自动恢复为可播放形象。",
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
    )
    staged_id_map = {**id_map, table: new_map}
    if table == "messages":
        await restore_conversation_context(db, rows, staged_id_map)
    elif table == "memories":
        await restore_memory_context(db, rows, staged_id_map, user_id, import_batch_id)
    return new_map, inserted


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
    id_map: dict[str, dict[str, int | str]] = {}
    # 未激活且 token 不外泄：该用户只承载预检行，结束即回滚。
    validation_user = User(
        username=f"backup-validation-{uuid.uuid4().hex}",
        activation_code="",
        activation_token_hash=hash_activation_token(generate_activation_token()),
        is_active=False,
    )
    try:
        db.add(validation_user)
        await db.flush()
        for table in BACKUP_RESTORE_ORDER:
            if table not in rows or table == "companion_actions":
                continue
            group = ("companion_action_packs", "companion_actions") if table == "companion_action_packs" else (table,)
            available_rows = {name: rows[name] for name in successful | set(group) if name in rows}
            staged_map = dict(id_map)
            try:
                async with db.begin_nested():
                    for member in group:
                        staged_map[member], _ = await _restore_table(
                            db,
                            member,
                            rows[member],
                            available_rows,
                            validation_user.id,
                            UrlRewriter({}),
                            staged_map,
                            mode=mode,
                            import_batch_id=import_batch_id,
                        )
                    if table == "companion_action_packs":
                        await asyncio.to_thread(
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
                            validation_user.id,
                            UrlRewriter({}),
                            write_files=False,
                        )
            except (KeyError, StatementError, TypeError, ValueError) as exc:
                logger.warning("backup table failed compatibility preflight", extra={"table": table})
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
        model = TABLE_MODELS[dependent_table]
        predicates = [model.user_id == target_user_id]
        if reference_column is not None:
            predicates.append(getattr(model, reference_column).is_not(None))
        if table == "cron_jobs":
            predicates.append(
                select(CronJob.id)
                .where(
                    CronJob.user_id == target_user_id,
                    model.source_key == COMPANION_CRON_SOURCE_PREFIX + cast(CronJob.id, String),
                )
                .exists(),
            )
        retained_id = await db.scalar(
            select(model.id).where(*predicates).limit(1),
        )
        if retained_id is not None:
            return True
    return False


async def _delete_user_rows(db: AsyncSession, table: str, user_id: int) -> None:
    model = TABLE_MODELS[table]
    await db.execute(delete(model).where(model.user_id == user_id))


async def _clear_compatible_rows(
    db: AsyncSession,
    target_user_id: int,
    compatible_rows: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], tuple[BackupImportFailure, ...]]:
    remaining = dict(compatible_rows)
    failures: list[BackupImportFailure] = []
    if ACTION_TABLES.issubset(remaining):
        action_failure = None
        for parent, field in (("avatar_assets", "avatar_id"), ("companion_outfits", "outfit_id")):
            if any(row.get(field) is not None for row in remaining["companion_action_packs"]) and (
                await _has_retained_dependent(db, parent, target_user_id, remaining)
            ):
                action_failure = "视频资产引用的身份或外观无法安全覆盖。"
                break
        if action_failure is None:
            try:
                async with db.begin_nested():
                    for table in ("companion_actions", "companion_action_packs"):
                        await _delete_user_rows(db, table, target_user_id)
            except IntegrityError:
                action_failure = "现有视频资产仍被其他内容引用，无法覆盖。"
        if action_failure is not None:
            for table in TABLES:
                if table in ACTION_TABLES:
                    failures.append(BackupImportFailure(table, len(remaining.pop(table)), action_failure))
    for table in reversed(TABLES):
        if table in ACTION_TABLES:
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
            )
            remaining.pop(table)
            failures.append(
                BackupImportFailure(
                    section=table,
                    count=len(compatible_rows[table]),
                    reason="目标现有数据仍被其他内容引用，无法安全覆盖此类别。",
                ),
            )
    if "conversations" not in remaining and "messages" in remaining:
        remaining.pop("messages")
        failures.append(
            BackupImportFailure(
                section="messages",
                count=len(compatible_rows["messages"]),
                reason="配套的会话无法安全覆盖，消息也已保留为目标端原数据。",
            ),
        )
    return remaining, tuple(failures)


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
    rewriter = UrlRewriter({})
    existing_scene_versions = (
        await db.execute(
            select(Persona.scene_state_version, Persona.scene_switch_version).where(Persona.user_id == target_user_id),
        )
    ).one_or_none()
    try:
        if mode == "overwrite":
            compatible_rows, clear_failures = await _clear_compatible_rows(db, target_user_id, compatible_rows)
            failures = (*failures, *clear_failures)
        id_map: dict[str, dict[str, int | str]] = {}
        imported: dict[str, int] = {}
        if "conversations" in compatible_rows:
            id_map["conversations"], imported["conversations"] = await _restore_table(
                db,
                "conversations",
                compatible_rows["conversations"],
                compatible_rows,
                target_user_id,
                rewriter,
                id_map,
                mode=mode,
                import_batch_id=import_batch_id,
            )
        file_result = await asyncio.to_thread(
            restore_files,
            extract_root,
            source_user_id,
            target_user_id,
            conversations=id_map.get("conversations", {}),
        )
        rewriter = file_result.rewriter
        if file_result.skipped_conversation_files:
            failures = (
                *failures,
                BackupImportFailure(
                    section="asset_files",
                    count=file_result.skipped_conversation_files,
                    reason="对应会话未能恢复，附件缺少可用的目标会话。",
                ),
            )
        for table in BACKUP_RESTORE_ORDER:
            if table == "conversations" or table not in compatible_rows:
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
            )
        if ACTION_TABLES.issubset(imported):
            await restore_action_catalogs(
                db,
                compatible_rows,
                id_map,
                target_user_id,
                rewriter,
                write_files=True,
            )
        if imported.get("personas") or imported.get("companion_scenes"):
            persona = await db.scalar(
                select(Persona).where(Persona.user_id == target_user_id).execution_options(populate_existing=True),
            )
            if persona is not None:
                previous_state, previous_switch = existing_scene_versions or (0, 0)
                persona.scene_state_version = max(persona.scene_state_version, previous_state) + 1
                persona.scene_switch_version = max(persona.scene_switch_version, previous_switch) + 1
                emit_ws_event(
                    db,
                    user_id=target_user_id,
                    event_type="companion.scene.updated",
                    payload={"version": persona.scene_state_version, "switch_version": persona.scene_switch_version},
                )
        return BackupRestoreResult(
            imported=imported,
            restored_files=len(rewriter.created),
            failures=failures,
            rewriter=rewriter,
        )
    except BaseException:
        rewriter.rollback()
        raise
