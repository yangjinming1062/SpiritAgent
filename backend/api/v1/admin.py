import asyncio
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from common import get_or_404, get_router
from components import (
    CAPABILITY_SERVICES,
    SETTINGS,
    AIConfig,
    DbSession,
    apply_partial,
    attachments_gc_session,
    get_logger,
    purge_user_temp_files,
    utc_now,
)
from fastapi import Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from modules.auth import (
    CurrentAdmin,
    LoginRecord,
    User,
    UserBackupImportFailure,
    UserBackupImportResponse,
    UserCreate,
    UserListResponse,
    UserModelConfig,
    UserModelConfigListItem,
    UserModelConfigListResponse,
    UserModelConfigRequest,
    UserResponse,
    UserUpdate,
    decode_activation_code,
    encode_activation_code,
    generate_activation_token,
    get_current_admin_token,
    hash_activation_token,
)
from modules.companion import Persona
from modules.conversation import Conversation
from modules.scheduler import (
    NightlyActivityLog,
    NightlyActivityLogItem,
    NightlyActivityLogListResponse,
)
from modules.system import MessageResponse
from services.adapters.desktop import terminate_user_gateway
from services.adapters.maintenance import user_maintenance
from services.application.configuration import get_system_settings_for_admin, save_system_settings
from services.domains.backup import (
    BACKUP_SECTION_IDS,
    TABLES,
    BackupArchiveError,
    BackupArchiveTooLargeError,
    BackupExportFileChangedError,
    BackupImportMode,
    BackupRestoreResult,
    collect_files_for_export,
    extract_backup_archive,
    load_backup_rows,
    load_manifest,
    restore_backup_rows,
    serialize_rows,
    tables_for_sections,
    write_backup_archive,
)
from services.domains.configuration import prepare_ai_config, public_ai_config
from services.domains.conversation import ensure_system_conversations_for_user
from services.infrastructure.assets import delete_user_assets
from services.infrastructure.llm import providers_supporting
from sqlalchemy import select, update
from starlette.background import BackgroundTask

logger = get_logger(__name__)

router = get_router(dependencies=[Depends(get_current_admin_token)])

# 备份 zip 分块读写；1 MB 分块减少线程池往返。
ARCHIVE_UPLOAD_CHUNK_BYTES = 1024 * 1024


@router.get("/users", response_model=UserListResponse)
async def list_users(db: DbSession) -> UserListResponse:
    users = (await db.execute(select(User).order_by(User.id))).scalars().all()
    return UserListResponse(items=[UserResponse.model_validate(user) for user in users])


@router.post("/users", response_model=UserResponse)
async def create_user(payload: UserCreate, db: DbSession) -> UserResponse:
    if (await db.execute(select(User).where(User.username == payload.username))).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在。")
    raw_token = generate_activation_token()
    code = encode_activation_code(payload.base_url, raw_token)
    user = User(
        username=payload.username,
        activation_code=code,
        activation_token_hash=hash_activation_token(raw_token),
        nightly_activity_enabled=payload.nightly_activity_enabled,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, payload: UserUpdate, db: DbSession) -> UserResponse:
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    if payload.regenerate_token or payload.base_url:
        # 激活码只由 create_user 生成；改地址保留原 token，重发则换新 token。
        base_url, raw_token = decode_activation_code(user.activation_code)
        if payload.regenerate_token:
            raw_token = generate_activation_token()
            user.activation_token_hash = hash_activation_token(raw_token)
        user.activation_code = encode_activation_code(payload.base_url or base_url, raw_token)
    apply_partial(user, payload, exclude={"regenerate_token", "base_url"})
    await db.commit()
    return UserResponse.model_validate(user)


def _purge_user_files(user_id: int, session_ids: list[str]) -> None:
    """删除与备份导出同范围的用户文件：资产目录、各会话附件与名下临时文件。"""
    delete_user_assets(user_id)
    for session_id in session_ids:
        attachments_gc_session(session_id)
    purge_user_temp_files(user_id)


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_user(user_id: int, db: DbSession) -> MessageResponse:
    """被遗忘权：在维护边界内停稳该用户的运行时，先删文件再删行（外键级联清理其余数据）；文件先于行删除，任一步失败都保留用户行，管理员重试即可继续清理。"""
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    # 维护边界可能等待在途付费任务数分钟，文件删除也可能较慢；等待期间不持有数据库事务，进入边界后重新加载用户。
    await db.rollback()
    async with user_maintenance(user_id):
        user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
        session_ids = [
            str(conv_id) for conv_id in await db.scalars(select(Conversation.id).where(Conversation.user_id == user_id))
        ]
        await db.rollback()
        await asyncio.to_thread(_purge_user_files, user_id, session_ids)
        await db.delete(user)
        await db.commit()
    return MessageResponse(message="用户已删除。")


@router.patch("/users/{user_id}/toggle-active", response_model=UserResponse)
async def toggle_user_active(user_id: int, db: DbSession) -> UserResponse:
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    user.is_active = not user.is_active
    if not user.is_active:
        await db.execute(
            update(LoginRecord)
            .where(LoginRecord.user_id == user_id, LoginRecord.is_active.is_(True))
            .values(is_active=False, logout_at=utc_now()),
        )
    await db.commit()
    if not user.is_active:
        await terminate_user_gateway(user_id)
    return UserResponse.model_validate(user)


def _config_list_item(r: UserModelConfig) -> UserModelConfigListItem:
    return UserModelConfigListItem(user_id=r.user_id, ai_config=public_ai_config(AIConfig.model_validate(r.ai_config)))


@router.get("/model-configs", response_model=UserModelConfigListResponse)
async def list_model_configs(db: DbSession) -> UserModelConfigListResponse:
    support = {service: providers_supporting(service) for service in CAPABILITY_SERVICES}
    return UserModelConfigListResponse(
        items=[_config_list_item(r) for r in (await db.execute(select(UserModelConfig))).scalars().all()],
        ai_provider_support=support,
        system_ai_config=public_ai_config(SETTINGS.ai_config),
    )


@router.get("/runtime-info")
async def runtime_info() -> dict[str, str]:
    """把 ``public_base_url`` 暴露给 admin 页：创建账号时自动填进激活码的 ``baseUrl``，留空时前端再降级到 ``http://localhost:10620``。"""
    return {"public_base_url": SETTINGS.public_base_url}


@router.get("/system-settings")
async def get_system_settings() -> dict[str, Any]:
    """获取系统全部动态配置项（敏感 Key 自动脱敏）。"""
    return get_system_settings_for_admin()


@router.put("/system-settings")
async def update_system_settings(payload: dict[str, Any], db: DbSession) -> dict[str, Any]:
    """更新系统动态配置，实时持久化到数据库并热重载生效。"""
    try:
        await save_system_settings(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return get_system_settings_for_admin()


@router.get("/nightly-activity-logs", response_model=NightlyActivityLogListResponse)
async def list_nightly_activity_logs(
    db: DbSession,
    user_id: int | None = None,
    target_date: date | None = None,
) -> NightlyActivityLogListResponse:
    """查询夜间自主活动日志。可选按 user_id 和 target_date 过滤。"""
    stmt = select(NightlyActivityLog).order_by(NightlyActivityLog.id.desc()).limit(300)
    if user_id is not None:
        stmt = stmt.where(NightlyActivityLog.user_id == user_id)
    if target_date is not None:
        stmt = stmt.where(NightlyActivityLog.target_date == target_date)
    rows = (await db.execute(stmt)).scalars().all()
    return NightlyActivityLogListResponse(items=[NightlyActivityLogItem.model_validate(row) for row in rows])


@router.put("/{user_id}/model-config")
async def upsert_model_config(user_id: int, payload: UserModelConfigRequest, db: DbSession) -> MessageResponse:
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()
    previous = AIConfig.model_validate(config.ai_config) if config else None
    try:
        ai_config = prepare_ai_config(payload.ai_config, previous)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if config:
        config.ai_config = ai_config.model_dump()
    else:
        db.add(UserModelConfig(user_id=user_id, ai_config=ai_config.model_dump()))
    await db.commit()
    return MessageResponse(message="模型配置已更新。")


@router.delete("/{user_id}/model-config")
async def delete_model_config(user_id: int, db: DbSession) -> MessageResponse:
    await db.delete(await get_or_404(db, UserModelConfig, user_id=user_id, detail="模型配置不存在。"))
    await db.commit()
    return MessageResponse(message="模型配置已删除。")


@router.get("/users/{user_id}/export")
async def export_user_backup(
    user_id: int,
    admin: CurrentAdmin,
    db: DbSession,
) -> FileResponse:
    """始终导出全量数据；恢复范围在导入时选择。"""
    # 各表在同一只读快照中读取，并发写入不会让会话与消息等关联表相互错位；鉴权依赖已让会话开启事务，须先结束才能设置隔离级别。
    await db.rollback()
    await db.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    rows_by_table = {tbl: await serialize_rows(db, tbl, user_id) for tbl in TABLES}
    # 用 commit 而非 rollback 结束快照：rollback 会使 user 过期，打包线程读取其属性将触发懒加载；打包与下载可能持续数分钟，不能占着事务。
    await db.commit()
    files = await asyncio.to_thread(collect_files_for_export, user_id, rows_by_table)

    fd, tmp = tempfile.mkstemp(prefix="spiritagent-export-", suffix=".zip")
    os.close(fd)
    try:
        await asyncio.to_thread(write_backup_archive, Path(tmp), user, admin, rows_by_table, files)
        filename = f"spiritagent-user-{user_id}-{datetime.now(UTC):%Y%m%d%H%M%S}.zip"
        return FileResponse(
            path=tmp,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(lambda p=tmp: Path(p).unlink(missing_ok=True)),
        )
    except BackupExportFileChangedError as exc:
        Path(tmp).unlink(missing_ok=True)
        logger.info("backup export media changed; retry required", extra={"user_id": user_id})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


@router.post("/users/{user_id}/import", response_model=UserBackupImportResponse)
async def import_user_backup(
    user_id: int,
    db: DbSession,
    file: UploadFile = File(...),
    mode: BackupImportMode = "overwrite",
    sections: list[str] | None = Query(
        default=None,
        description=f"恢复范围分组，可选 {', '.join(BACKUP_SECTION_IDS)} 或 all；缺省为全部",
    ),
) -> UserBackupImportResponse:
    """尽力恢复备份；覆盖仅清理兼容且可安全替换的数据类。只写入所选分组，不动未勾选内容。"""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件必须是 .zip。")
    try:
        selected_tables = tables_for_sections(sections)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    resolved_sections = (
        list(BACKUP_SECTION_IDS)
        if sections is None or "all" in {item.strip() for item in sections if item and item.strip()}
        else [item.strip() for item in sections if item and item.strip()]
    )
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    # 上传落盘、解压与维护等待都可能持续数分钟，期间不持有数据库事务。
    await db.rollback()

    with tempfile.TemporaryDirectory(prefix="spiritagent-import-") as tmp_dir:
        zip_path = Path(tmp_dir) / "upload.zip"
        extract_root = Path(tmp_dir) / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        # 分块 spool，避免大压缩包整体驻留内存
        with open(zip_path, "wb") as out:
            while chunk := await file.read(ARCHIVE_UPLOAD_CHUNK_BYTES):
                await asyncio.to_thread(out.write, chunk)

        try:
            await asyncio.to_thread(extract_backup_archive, zip_path, extract_root)
        except BackupArchiveTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except BackupArchiveError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        try:
            manifest = await asyncio.to_thread(load_manifest, extract_root)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"备份无效：{exc}") from exc
        source_uid = int(manifest["source_user_id"])
        read_result = await asyncio.to_thread(
            load_backup_rows,
            extract_root,
            list(manifest["tables"]),
            dict(manifest["row_counts"]),
            selected_tables=selected_tables,
        )
        restore_result: BackupRestoreResult | None = None
        async with user_maintenance(user_id):
            await get_or_404(db, User, id=user_id, detail="用户不存在。")
            try:
                restore_result = await restore_backup_rows(
                    db,
                    extract_root,
                    source_uid,
                    user_id,
                    read_result.rows,
                    mode=mode,
                )
                if await db.scalar(select(Persona.is_complete).where(Persona.user_id == user_id)):
                    await ensure_system_conversations_for_user(db, user_id)
                else:
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
                    raise HTTPException(status_code=503, detail="目标存储暂时不可用，请稍后重试。") from exc
                raise HTTPException(status_code=400, detail="备份数据无法写入目标账户，请核对恢复范围。") from exc
            except BaseException as exc:
                await db.rollback()
                if restore_result is not None:
                    restore_result.rewriter.rollback()
                if not isinstance(exc, asyncio.CancelledError):
                    logger.exception("backup restore failed unexpectedly", extra={"user_id": user_id})
                raise

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
