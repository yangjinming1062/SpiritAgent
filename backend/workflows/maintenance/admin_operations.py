import asyncio
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from features.accounts.backup import BackupUserSnapshot
from features.accounts.queries import delete_account_record, require_account
from features.accounts.schemas import UserBackupImportFailure, UserBackupImportResponse
from features.companion.readiness import persona_is_complete
from features.configuration.lock import configuration_lock
from features.conversation.bootstrap import ensure_main_conversation_for_user
from features.conversation.identifiers import conversation_ids
from features.media.chain import spawn_removed_video_task_cancellation
from features.media.video_queue.service import anonymize_user_video_queue
from infrastructure.assets.asset_store import delete_user_assets
from infrastructure.async_io import run_blocking
from infrastructure.attachments import gc_session as attachments_gc_session
from infrastructure.backup import BACKUP_REGISTRY, BackupImportMode
from infrastructure.file_results import DeferredCleanup, FileResult
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.temp_files import purge_user as purge_user_temp_files
from infrastructure.uploads import UploadSource
from runtime.responses import MessageResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from workflows.maintenance.backup.archive import (
    BackupArchiveError,
    BackupArchiveTooLargeError,
    BackupExportFileChangedError,
    extract_backup_archive,
    write_backup_archive,
)
from workflows.maintenance.backup.file_packing import collect_files_for_export
from workflows.maintenance.backup.manifest import load_manifest
from workflows.maintenance.backup.restoration import BackupRestoreResult, load_backup_rows, restore_backup_rows
from workflows.maintenance.backup.serializers import (
    TABLES,
    tables_for_sections,
)
from workflows.maintenance.user import user_maintenance

logger = get_logger(__name__)


ARCHIVE_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _purge_user_files(user_id: int, session_ids: list[str]) -> None:
    """删除与备份导出同范围的用户文件：资产目录、各会话附件与名下临时文件。"""
    delete_user_assets(user_id)
    for session_id in session_ids:
        attachments_gc_session(session_id)
    purge_user_temp_files(user_id)


async def delete_user(user_id: int, db: AsyncSession) -> MessageResponse:
    """被遗忘权：在维护边界内停稳该用户的运行时，先删文件再删行（外键级联清理其余数据）；文件先于行删除，任一步失败都保留用户行，管理员重试即可继续清理。"""
    await require_account(db, user_id)
    # 维护边界可能等待在途付费任务数分钟，文件删除也可能较慢；等待期间不持有数据库事务，进入边界后重新加载用户。
    await db.rollback()
    async with user_maintenance(user_id):
        await require_account(db, user_id)
        session_ids = [str(conv_id) for conv_id in await conversation_ids(db, user_id)]
        await db.rollback()
        await anonymize_user_video_queue(user_id)
        await run_blocking(_purge_user_files, user_id, session_ids)
        await delete_account_record(db, user_id)
        await db.commit()
    return MessageResponse(message="用户已删除。")


async def export_user_backup(user_id: int, admin: str, db: AsyncSession) -> FileResult:
    """始终导出全量数据；恢复范围在导入时选择。"""
    # 各表在同一只读快照中读取，并发写入不会让会话与消息等关联表相互错位；鉴权依赖已让会话开启事务，须先结束才能设置隔离级别。
    await db.rollback()
    await db.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
    user = await require_account(db, user_id)
    rows_by_table = {table: await BACKUP_REGISTRY.table(table).export(db, user_id) for table in TABLES}
    # 用 commit 而非 rollback 结束快照：rollback 会使 user 过期，打包线程读取其属性将触发懒加载；打包与下载可能持续数分钟，不能占着事务。
    await db.commit()
    files = await run_blocking(collect_files_for_export, user_id, rows_by_table)

    fd, tmp = tempfile.mkstemp(prefix="spiritagent-export-", suffix=".zip")
    os.close(fd)
    try:
        await run_blocking(
            write_backup_archive,
            Path(tmp),
            BackupUserSnapshot(user.id, user.username),
            admin,
            rows_by_table,
            files,
        )
        filename = f"spiritagent-user-{user_id}-{datetime.now(UTC):%Y%m%d%H%M%S}.zip"
        return FileResult(
            path=tmp,
            media_type="application/zip",
            filename=filename,
            background=DeferredCleanup(lambda p=tmp: Path(p).unlink(missing_ok=True)),
        )
    except BackupExportFileChangedError as exc:
        Path(tmp).unlink(missing_ok=True)
        logger.info("backup export media changed; retry required", extra={"user_id": user_id})
        raise RequestError(status_code=409, message=str(exc)) from exc
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


async def import_user_backup(
    user_id: int,
    db: AsyncSession,
    file: UploadSource,
    mode: BackupImportMode,
    sections: list[str] | None,
) -> UserBackupImportResponse:
    """预检选中数据图后原子恢复，失败不改变目标账户。"""
    if not file.filename or not file.filename.endswith(".zip"):
        raise RequestError(status_code=status.HTTP_400_BAD_REQUEST, message="上传文件必须是 .zip。")
    try:
        selected_tables, resolved_sections = tables_for_sections(sections)
    except ValueError as exc:
        raise RequestError(status_code=status.HTTP_400_BAD_REQUEST, message=str(exc)) from exc
    await require_account(db, user_id)
    # 上传落盘、解压与维护等待都可能持续数分钟，期间不持有数据库事务。
    await db.commit()

    with tempfile.TemporaryDirectory(prefix="spiritagent-import-") as tmp_dir:
        zip_path = Path(tmp_dir) / "upload.zip"
        extract_root = Path(tmp_dir) / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        # 分块 spool，避免大压缩包整体驻留内存
        with open(zip_path, "wb") as out:
            await run_blocking(shutil.copyfileobj, file.file, out, ARCHIVE_UPLOAD_CHUNK_BYTES)

        try:
            await run_blocking(extract_backup_archive, zip_path, extract_root)
        except BackupArchiveTooLargeError as exc:
            raise RequestError(status_code=413, message=str(exc)) from exc
        except BackupArchiveError as exc:
            raise RequestError(status_code=status.HTTP_400_BAD_REQUEST, message=str(exc)) from exc
        except OSError as exc:
            raise RequestError(status_code=503, message="备份解压存储暂时不可用，请稍后重试。") from exc

        try:
            manifest = await run_blocking(load_manifest, extract_root)
        except ValueError as exc:
            raise RequestError(status_code=status.HTTP_400_BAD_REQUEST, message=f"备份无效：{exc}") from exc
        source_uid = int(manifest["source_user_id"])
        try:
            read_result = await run_blocking(
                load_backup_rows,
                extract_root,
                list(manifest["tables"]),
                dict(manifest["row_counts"]),
                selected_tables=selected_tables,
            )
        except ValueError as exc:
            raise RequestError(status_code=400, message="备份数据结构或任务关联无效，请核对备份内容。") from exc
        if read_result.failures:
            return UserBackupImportResponse(
                mode=mode,
                sections=resolved_sections,
                imported={},
                restored_files=0,
                failed=[
                    UserBackupImportFailure(section=item.section, count=item.count, reason=item.reason)
                    for item in read_result.failures
                ],
            )
        restore_result: BackupRestoreResult | None = None
        async with user_maintenance(user_id), configuration_lock:
            await require_account(db, user_id)
            try:
                restore_result = await restore_backup_rows(
                    db,
                    extract_root,
                    source_uid,
                    user_id,
                    read_result.rows,
                    mode=mode,
                )
                if restore_result.failures:
                    await db.rollback()
                    restore_result.rewriter.rollback()
                    return UserBackupImportResponse(
                        mode=mode,
                        sections=resolved_sections,
                        imported={},
                        restored_files=0,
                        failed=[
                            UserBackupImportFailure(section=item.section, count=item.count, reason=item.reason)
                            for item in restore_result.failures
                        ],
                    )
                if await persona_is_complete(db, user_id):
                    await ensure_main_conversation_for_user(db, user_id, commit=False)
                await db.commit()
            except (ValueError, OSError) as exc:
                await db.rollback()
                if restore_result is not None:
                    restore_result.rewriter.rollback()
                logger.warning(
                    "backup restore failed",
                    extra={"user_id": user_id, "error_type": type(exc).__name__},
                    exc_info=True,
                )
                if isinstance(exc, OSError):
                    raise RequestError(status_code=503, message="目标存储暂时不可用，请稍后重试。") from exc
                raise RequestError(status_code=400, message="备份数据无法写入目标账户，请核对恢复范围。") from exc
            except BaseException as exc:
                await db.rollback()
                if restore_result is not None:
                    restore_result.rewriter.rollback()
                if not isinstance(exc, asyncio.CancelledError):
                    logger.exception("backup restore failed unexpectedly", extra={"user_id": user_id})
                raise
            spawn_removed_video_task_cancellation(user_id, restore_result.removed_visual_tasks)

    failed = [
        UserBackupImportFailure(section=item.section, count=item.count, reason=item.reason)
        for item in (*read_result.failures, *restore_result.failures)
    ]
    return UserBackupImportResponse(
        mode=mode,
        sections=resolved_sections,
        imported=restore_result.imported,
        restored_files=restore_result.restored_files,
        failed=failed,
    )
