import asyncio
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from components import SETTINGS, get_logger
from modules.auth import User, generate_activation_token, hash_activation_token
from modules.companion import COMPANION_CRON_SOURCE_PREFIX, Persona
from modules.conversation import Conversation
from modules.memory import Memory
from modules.scheduler import CronJob
from modules.settings import resolve_user_timezone
from modules.ws import emit_ws_event
from sqlalchemy import String, cast, delete, or_, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.assets import collect_live_asset_paths, collect_message_asset_releases, enqueue_asset_cleanup
from services.domains.memory import rebuild_diary_indexes
from services.infrastructure.assets import user_asset_lock

from .action_assets import restore_action_catalogs, validate_action_files
from .asset_layout import freeze_asset_directory, plan_asset_layout
from .file_packing import UrlRewriter, planned_asset_mapping, referenced_backup_files, restore_files, validate_row_files
from .serializers import (
    ACTION_TABLES,
    ATOMIC_SECTION_GROUPS,
    CONVERSATION_TABLES,
    IDENTITY_BLOCKED_REASON,
    IDENTITY_DEPENDENT_REASON,
    IDENTITY_GROUP,
    IDENTITY_INCOMPLETE_REASON,
    IDENTITY_TABLES,
    POST_TABLES,
    RETIRED_TABLES,
    TABLE_MODELS,
    TABLES,
    BackupImportMode,
    insert_rows,
    read_table_rows,
    restore_conversation_context,
    restore_memory_context,
    validate_conversation_merge,
)

logger = get_logger(__name__)

# 这些表的 SQL 参数含供应商密钥，数据库错误的堆栈不写入日志。
_SECRET_TABLES = frozenset({"user_model_configs"})
BACKUP_RESTORE_ORDER: tuple[str, ...] = (
    "conversations",
    "messages",
    *(table for table in TABLES if table not in CONVERSATION_TABLES),
)
# 父类覆盖会改变仍被保留的子类引用，子类未随本次恢复清理时须保留父类；None 表示引用嵌在 JSON / 数组等非关系列中，只要存在保留行就按可能有关联处理。
OVERWRITE_DEPENDENT_REFERENCES: dict[str, tuple[tuple[str, str | None], ...]] = {
    "avatar_assets": (
        ("companion_character_cards", "avatar_id"),
        ("companion_action_packs", "avatar_id"),
    ),
    "companion_outfits": (("companion_action_packs", "outfit_id"),),
    "companion_action_packs": (("companion_actions", "pack_id"),),
    "cron_jobs": (("companion_intents", "source_key"),),
    "conversations": (
        ("cron_jobs", "conversation_id"),
        ("memories", None),
    ),
    "companion_scenes": (("personas", "active_scene_id"),),
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
    # 旧版已废弃表（如片刻）静默跳过；其后继表缺失视为“没有动态”，不报失败。
    retired_present = bool(RETIRED_TABLES & manifest_set)
    failures = [
        BackupImportFailure(
            section=table,
            count=row_counts[table],
            reason="当前版本不支持此数据类别。",
        )
        for table in manifest_tables
        if table not in TABLES and table not in RETIRED_TABLES
    ]
    # 仅在显式勾选子集时报告“备份未包含”；全量导入对历史缺表保持静默。
    if selected_tables is not None and selected_tables != frozenset(TABLES):
        for table in sorted(wanted - manifest_set):
            if table not in TABLES:
                continue
            if retired_present and table in {"companion_posts", "companion_post_comments"}:
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
                reason="会话与消息必须同时存在，无法单独恢复。",
            )
            for table in manifest_tables
            if table in incomplete_conversation_tables
        )

    rows: dict[str, list[dict[str, Any]]] = {}
    for table in manifest_tables:
        if (
            table not in wanted
            or table not in TABLES
            or table in RETIRED_TABLES
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
                else "会话与消息必须同时恢复。"
                if group == CONVERSATION_TABLES
                else "动态与评论必须同时恢复。"
                if group == POST_TABLES
                else "动作包与动作必须同时恢复。"
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
    # 成组约束已丢弃半套动作表；包内根本没有动作表但外观曾启动视频时单独提示。
    if ACTION_TABLES & wanted and not ACTION_TABLES.issubset(rows) and not ACTION_TABLES & manifest_set:
        started = sum(bool(row.get("initial_video_started")) for row in rows.get("companion_outfits", []))
        if started:
            failures.append(
                BackupImportFailure(
                    "companion_action_packs",
                    started,
                    "备份未包含动作包和动作记录，视频文件无法自动恢复为可播放形象。",
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
    elif table == "messages":
        await restore_conversation_context(db, rows, staged_id_map)
    elif table == "memories":
        await restore_memory_context(db, rows, staged_id_map, user_id, import_batch_id)
    elif table == "companion_diary_entries":
        await rebuild_diary_indexes(db, user_id)
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
    conversation_merge_conflict: str | None = None
    if mode == "merge":
        for raw in rows.get("conversations", []):
            if raw.get("kind") != "special" or not raw.get("system_preset_id"):
                continue
            existing = await db.scalar(
                select(Conversation).where(
                    Conversation.user_id == target_user_id,
                    Conversation.kind == "special",
                    Conversation.system_preset_id == raw["system_preset_id"],
                ),
            )
            if existing is not None:
                try:
                    await validate_conversation_merge(db, existing, raw)
                except ValueError as exc:
                    conversation_merge_conflict = str(exc)
                    break
    id_map: dict[str, dict[str, int | str]] = {}
    rewriter = UrlRewriter(
        await asyncio.to_thread(planned_asset_mapping, extract_root, source_user_id, target_user_id),
        source_user_id=source_user_id,
        target_user_id=target_user_id,
        extract_root=extract_root,
        conversation_ids=frozenset(str(row["id"]) for row in rows.get("conversations", [])),
    )
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
            if table not in rows:
                continue
            if table == "companion_actions":
                continue
            if table == "messages" and "conversations" in rows:
                continue
            if table == "companion_post_comments" and "companion_posts" in rows:
                continue
            # 身份三表与动作两表各自成组预检，避免只写通一半。
            if table in {"companion_character_cards", "personas"} and "avatar_assets" in rows:
                continue
            if table == "conversations":
                group = ("conversations", "messages")
            elif table == "avatar_assets" and IDENTITY_TABLES & set(rows):
                group = tuple(member for member in IDENTITY_GROUP if member in rows)
            elif table == "companion_action_packs":
                group = ("companion_action_packs", "companion_actions")
            elif table == "companion_posts":
                group = ("companion_posts", "companion_post_comments")
            else:
                group = (table,)
            available_rows = {name: rows[name] for name in successful | set(group) if name in rows}
            staged_map = dict(id_map)
            try:
                if table == "conversations" and conversation_merge_conflict is not None:
                    raise ValueError(conversation_merge_conflict)
                async with db.begin_nested():
                    for member in group:
                        await asyncio.to_thread(validate_row_files, member, rows[member], rewriter)
                        staged_map[member], _ = await _restore_table(
                            db,
                            member,
                            rows[member],
                            available_rows,
                            validation_user.id,
                            rewriter,
                            staged_map,
                            mode=mode,
                            import_batch_id=import_batch_id,
                            # 预检行属于校验用户，包内资产仍按恢复后的真实归属判断。
                            asset_owner_id=target_user_id,
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
    stmt = delete(model).where(model.user_id == user_id)
    if table == "conversations":
        conversation_ids = list(await db.scalars(select(Conversation.id).where(Conversation.user_id == user_id)))
        # 与编辑、撤回同序：持用户资产锁完成引用登记与删除，避免与视频入队交错。
        async with user_asset_lock(user_id):
            await collect_message_asset_releases(db, user_id, conversation_ids)
            await db.execute(stmt)
        return
    if table == "memories":
        stmt = stmt.where(Memory.source_kind != "diary", or_(Memory.context.is_(None), ~Memory.context.like("diary:%")))
    elif table == "companion_diary_entries":
        await db.execute(
            delete(Memory).where(
                Memory.user_id == user_id,
                Memory.system_preset_id == "companion",
                or_(Memory.source_kind == "diary", Memory.context.like("diary:%")),
            ),
        )
    await db.execute(stmt)


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
    if ACTION_TABLES.issubset(remaining):
        action_failure = None
        for parent, field in (("avatar_assets", "avatar_id"), ("companion_outfits", "outfit_id")):
            if (
                parent in remaining or any(row.get(field) is not None for row in remaining["companion_action_packs"])
            ) and (await _has_retained_dependent(db, parent, target_user_id, remaining)):
                action_failure = "动作包引用的身份或外观无法安全覆盖。"
                break
        if action_failure is None:
            try:
                async with db.begin_nested():
                    for table in ("companion_actions", "companion_action_packs"):
                        await _delete_user_rows(db, table, target_user_id)
            except IntegrityError:
                logger.warning(
                    "backup action tables could not be cleared without affecting retained data",
                    extra={"target_user_id": target_user_id},
                    exc_info=True,
                )
                action_failure = "现有动作包仍被其他内容引用，无法覆盖。"
        if action_failure is not None:
            for table in TABLES:
                if table in ACTION_TABLES:
                    failures.append(BackupImportFailure(table, len(remaining.pop(table)), action_failure))

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
        for table in ("companion_action_packs", "companion_actions"):
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
        if table in ACTION_TABLES or table in IDENTITY_TABLES:
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
    if "conversations" not in remaining and "messages" in remaining:
        remaining.pop("messages")
        failures.append(
            BackupImportFailure(
                section="messages",
                count=len(compatible_rows["messages"]),
                reason="配套的会话无法安全覆盖，消息也已保留为目标端原数据。",
            ),
        )
    # 覆盖清理后仍须成组：身份缺一即整组不写，避免角色卡引用未写入的头像。
    for group in (IDENTITY_TABLES, ACTION_TABLES):
        present = group & set(remaining)
        if present and present != group:
            reason = IDENTITY_INCOMPLETE_REASON if group == IDENTITY_TABLES else "动作包与动作必须同时恢复。"
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
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            task.result()
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
    existing_scene_versions = (
        await db.execute(
            select(Persona.scene_state_version, Persona.scene_switch_version).where(Persona.user_id == target_user_id),
        )
    ).one_or_none()
    try:
        if mode == "overwrite":
            await enqueue_asset_cleanup(db, target_user_id, await collect_live_asset_paths(db, target_user_id))
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
                asset_owner_id=target_user_id,
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
                asset_owner_id=target_user_id,
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
            referenced_files=await asyncio.to_thread(referenced_backup_files, extract_root, compatible_rows),
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
        await asyncio.to_thread(rewriter.rewrite, compatible_rows)
        for table, original_id in rewriter.inserted_rows:
            row = await db.get(TABLE_MODELS[table], id_map[table][original_id])
            mapping = layout.rows.get((table, original_id), layout.mapping)
            aliases = {
                old.replace(f"companion-assets/{source_user_id}/", f"companion-assets/{target_user_id}/", 1): new
                for old, new in mapping.items()
            }
            remap = UrlRewriter({**mapping, **aliases})
            for column in row.__table__.columns:
                value = getattr(row, column.name)
                if isinstance(value, str | dict | list):
                    rewritten = freeze_asset_directory(
                        table,
                        column.name,
                        remap.rewrite(value),
                        layout.directories[(table, original_id)],
                    )
                    if rewritten != value:
                        setattr(row, column.name, rewritten)
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
        )
    except BaseException:
        rewriter.rollback()
        raise
