import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from common import get_or_404, get_router, list_response
from components import CAPABILITY_SERVICES, SETTINGS, DbSession, apply_partial, get_logger, load_ai_config, utc_now
from fastapi import Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from modules.auth import (
    CurrentAdmin,
    LoginRecord,
    User,
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
from modules.companion import AvatarAsset, Companion3DModel, Persona
from modules.scheduler import (
    NightlyActivityLog,
    NightlyActivityLogItem,
    NightlyActivityLogListResponse,
)
from modules.system import MessageResponse
from services.adapters.desktop.handlers import terminate_user_gateway
from services.adapters.maintenance import user_maintenance
from services.application.configuration.system_settings import get_system_settings_for_admin, save_system_settings
from services.application.generation import delete_portrait_file
from services.domains.backup import (
    CONVERSATION_TABLES,
    TABLES,
    UrlRewriter,
    build_manifest,
    clear_user_scoped_rows,
    collect_files_for_export,
    deserialize_rows,
    insert_rows,
    load_manifest,
    restore_files,
    serialize_rows,
)
from services.domains.configuration.ai_config import prepare_ai_config, public_ai_config
from services.domains.conversation import ensure_system_conversations_for_user
from services.infrastructure.llm import providers_supporting
from sqlalchemy import delete, select, update
from starlette.background import BackgroundTask

logger = get_logger(__name__)

router = get_router(dependencies=[Depends(get_current_admin_token)])

# 用户备份 zip 大小上限 500 MB；上传也按此截断以免 OOM。
ARCHIVE_MAX_BYTES = 500 * 1024 * 1024
# 1 MB 分块上传，匹配 update.py 的 CHUNK_SIZE 数量级。
ARCHIVE_UPLOAD_CHUNK_BYTES = 1024 * 1024


@router.get("/users", response_model=UserListResponse)
async def list_users(db: DbSession) -> UserListResponse:
    return list_response(
        (await db.execute(select(User).order_by(User.id))).scalars().all(),
        UserResponse,
        UserListResponse,
    )


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: DbSession) -> UserResponse:
    return UserResponse.model_validate(await get_or_404(db, User, id=user_id, detail="用户不存在。"))


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
    if payload.regenerate_token:
        raw_token = generate_activation_token()
        user.activation_token_hash = hash_activation_token(raw_token)
        base_url = payload.base_url
        if not base_url and user.activation_code:
            try:
                base_url = decode_activation_code(user.activation_code)[0]
            except Exception:
                base_url = "http://localhost:10620"
        user.activation_code = encode_activation_code(base_url or "http://localhost:10620", raw_token)
    elif payload.base_url:
        if user.activation_code:
            try:
                _, token = decode_activation_code(user.activation_code)
                user.activation_code = encode_activation_code(payload.base_url, token)
            except Exception:
                # activation_code 解码失败说明 token 已损坏：不能再以旧 code 当 fallback 让客户端连到老 host。
                # 行为对齐 regenerate_token 分支：默认 base_url 重发一个 token，渲染端能拿到新激活链接。
                raw_token = generate_activation_token()
                user.activation_token_hash = hash_activation_token(raw_token)
                user.activation_code = encode_activation_code(payload.base_url, raw_token)
    apply_partial(user, payload, exclude={"regenerate_token", "base_url"})
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_user(user_id: int, db: DbSession) -> MessageResponse:
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    await terminate_user_gateway(user_id)

    # 清除用户范围内的 DB 行与磁盘资产（被遗忘权）。
    avatar_rows = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id))).scalars().all()
    for av in avatar_rows:
        delete_portrait_file(av.asset_url)

    await db.execute(delete(AvatarAsset).where(AvatarAsset.user_id == user_id))
    await db.execute(delete(Companion3DModel).where(Companion3DModel.user_id == user_id))
    await db.delete(await db.get(User, user_id))
    await db.commit()

    for sub in ("companion-assets", "companion-models"):
        d = Path(SETTINGS.data_dir) / sub / str(user_id)
        if d.exists():
            with contextlib.suppress(Exception):
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
                else:
                    d.unlink(missing_ok=True)
    return {"message": "用户已删除。"}


@router.patch("/users/{user_id}/toggle-active")
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
    return UserModelConfigListItem(user_id=r.user_id, ai_config=public_ai_config(load_ai_config(r.ai_config)))


@router.get("/model-configs", response_model=UserModelConfigListResponse)
async def list_model_configs(db: DbSession) -> UserModelConfigListResponse:
    support = {service: providers_supporting(service) for service in CAPABILITY_SERVICES}
    return UserModelConfigListResponse(
        items=[_config_list_item(r) for r in (await db.execute(select(UserModelConfig))).scalars().all()],
        ai_provider_support=support,
        system_ai_config=public_ai_config(SETTINGS.ai_config),
    )


@router.get("/runtime-info")
async def runtime_info() -> dict:
    """把 ``public_base_url`` 暴露给 admin 页：创建账号时自动填进激活码的 ``baseUrl``，留空时前端再降级到 ``http://localhost:10620``。"""
    return {"public_base_url": SETTINGS.public_base_url or ""}


@router.get("/system-settings")
async def get_system_settings(db: DbSession) -> dict[str, Any]:
    """获取系统全部动态配置项（敏感 Key 自动脱敏）。"""
    return await get_system_settings_for_admin(db)


@router.put("/system-settings")
async def update_system_settings(payload: dict[str, Any], db: DbSession) -> dict[str, Any]:
    """更新系统动态配置，实时持久化到数据库并热重载生效。"""
    return await save_system_settings(db, payload)


@router.get("/nightly-activity-logs", response_model=NightlyActivityLogListResponse)
async def list_nightly_activity_logs(
    db: DbSession,
    user_id: int | None = None,
    target_date: str | None = None,
) -> NightlyActivityLogListResponse:
    """查询夜间自主活动日志。可选按 user_id 和 target_date 过滤。"""
    stmt = select(NightlyActivityLog).order_by(NightlyActivityLog.id.desc()).limit(300)
    if user_id is not None:
        stmt = stmt.where(NightlyActivityLog.user_id == user_id)
    if target_date:
        try:
            stmt = stmt.where(NightlyActivityLog.target_date == date.fromisoformat(target_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid target_date format, use YYYY-MM-DD")
    rows = (await db.execute(stmt)).scalars().all()
    return list_response(rows, NightlyActivityLogItem, NightlyActivityLogListResponse)


@router.put("/{user_id}/model-config")
async def upsert_model_config(user_id: int, payload: UserModelConfigRequest, db: DbSession) -> MessageResponse:
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()
    previous = load_ai_config(config.ai_config) if config else None
    ai_config = prepare_ai_config(payload.ai_config, previous)
    if config:
        config.ai_config = ai_config.model_dump()
    else:
        db.add(UserModelConfig(user_id=user_id, ai_config=ai_config.model_dump()))
    await db.commit()
    return {"message": "模型配置已更新。"}


@router.delete("/{user_id}/model-config")
async def delete_model_config(user_id: int, db: DbSession) -> MessageResponse:
    await db.delete(await get_or_404(db, UserModelConfig, user_id=user_id, detail="模型配置不存在。"))
    await db.commit()
    return {"message": "模型配置已删除。"}


# ── 用户数据导出 / 导入 ────────────────────────────────────────
# 会话与消息可选；channel_bindings / login_records / admin_sessions 不迁移；
# activation_code / activation_token_hash 也不复制（绑定原部署 base_url）。


def _create_export_zip(
    tmp_path: str,
    user: User,
    admin: CurrentAdmin,
    rows_by_table: dict[str, list[dict[str, Any]]],
    files: list[Path],
) -> None:
    data_dir_root = Path(SETTINGS.data_dir).resolve()
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = build_manifest(user, rows_by_table, admin)
        checksums: dict[str, str] = {}
        for tbl, rs in rows_by_table.items():
            content = json.dumps({"table": tbl, "rows": rs}, ensure_ascii=False).encode("utf-8")
            name = f"db/{tbl}.json"
            zf.writestr(name, content)
            checksums[name] = hashlib.sha256(content).hexdigest()
        for src in files:
            arc = "files/" + src.relative_to(data_dir_root).as_posix()
            with src.open("rb") as source, zf.open(arc, "w") as target:
                digest = hashlib.sha256()
                while chunk := source.read(ARCHIVE_UPLOAD_CHUNK_BYTES):
                    target.write(chunk)
                    digest.update(chunk)
            checksums[arc] = digest.hexdigest()
        manifest["checksums"] = checksums
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))


def _extract_and_validate_zip(zip_path: Path, extract_root: Path) -> None:
    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效 zip 文件。") from None

    try:
        if sum(entry.file_size for entry in zf.infolist()) > 4 * 1024**3:
            raise HTTPException(status_code=413, detail="备份解压后超过 4 GB。")
        if len(set(zf.namelist())) != len(zf.namelist()):
            raise HTTPException(status_code=400, detail="备份包含重复文件。")
        extract_resolved = extract_root.resolve()
        for name in zf.namelist():
            target_path = (extract_root / name).resolve()
            if not target_path.is_relative_to(extract_resolved):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"非法归档条目：{name}")
            if name.endswith("/"):
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
    finally:
        zf.close()


@router.get("/users/{user_id}/export")
async def export_user_backup(
    user_id: int,
    admin: CurrentAdmin,
    db: DbSession,
    include_conversations: bool = False,
) -> FileResponse:
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    rows_by_table = {
        tbl: await serialize_rows(db, tbl, user_id)
        for tbl in TABLES
        if include_conversations or tbl not in CONVERSATION_TABLES
    }
    files = await asyncio.to_thread(collect_files_for_export, user_id, rows_by_table)

    fd, tmp = tempfile.mkstemp(prefix="spiritagent-export-", suffix=".zip")
    os.close(fd)
    try:
        await asyncio.to_thread(_create_export_zip, tmp, user, admin, rows_by_table, files)
        filename = f"spiritagent-user-{user_id}-{datetime.utcnow():%Y%m%d%H%M%S}.zip"
        return FileResponse(
            path=tmp,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(lambda p=tmp: Path(p).unlink(missing_ok=True)),
        )
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


@router.post("/users/{user_id}/import")
async def import_user_backup(
    user_id: int,
    db: DbSession,
    file: UploadFile = File(...),
    mode: str = "overwrite",
) -> dict:
    """覆盖仅清理备份声明的表；合并保留已有唯一记录与激活形象。"""
    if mode not in ("overwrite", "merge"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode 必须是 overwrite 或 merge。")
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件必须是 .zip。")
    await get_or_404(db, User, id=user_id, detail="用户不存在。")

    with tempfile.TemporaryDirectory(prefix="spiritagent-import-") as tmp_dir:
        zip_path = Path(tmp_dir) / "upload.zip"
        extract_root = Path(tmp_dir) / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        # 分块 spool：边读边写边累加，超 500 MB 直接拒
        total = 0
        with open(zip_path, "wb") as out:
            while chunk := await file.read(ARCHIVE_UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > ARCHIVE_MAX_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Archive exceeds {ARCHIVE_MAX_BYTES} bytes",
                    )
                await asyncio.to_thread(out.write, chunk)

        await asyncio.to_thread(_extract_and_validate_zip, zip_path, extract_root)

        manifest = await asyncio.to_thread(load_manifest, extract_root)
        source_uid = int(manifest["source_user_id"])
        rewriter = UrlRewriter({})
        boundary = user_maintenance(user_id) if mode == "overwrite" else contextlib.nullcontext()
        async with boundary:
            try:
                rows = await asyncio.to_thread(deserialize_rows, extract_root, manifest["tables"])
                if any(len(records) != manifest.get("row_counts", {}).get(table) for table, records in rows.items()):
                    raise ValueError("Backup row counts do not match manifest")
                if mode == "overwrite":
                    await clear_user_scoped_rows(db, user_id, manifest["tables"])
                id_map: dict[str, dict[str, int | str]] = {}
                imported: dict[str, int] = {}
                if "conversations" in rows:
                    id_map["conversations"], imported["conversations"] = await insert_rows(
                        db,
                        "conversations",
                        rows["conversations"],
                        user_id,
                        rewriter,
                        id_map,
                        mode=mode,
                    )
                rewriter = await asyncio.to_thread(
                    restore_files,
                    extract_root,
                    source_uid,
                    user_id,
                    conversations=id_map.get("conversations", {}),
                )
                for tbl in TABLES:
                    if tbl in rows and tbl != "conversations":
                        id_map[tbl], imported[tbl] = await insert_rows(
                            db,
                            tbl,
                            rows[tbl],
                            user_id,
                            rewriter,
                            id_map,
                            mode=mode,
                        )
                if await db.scalar(select(Persona.is_complete).where(Persona.user_id == user_id)):
                    await ensure_system_conversations_for_user(db, user_id)
                else:
                    await db.commit()
            except (ValueError, OSError, KeyError, TypeError) as exc:
                await db.rollback()
                rewriter.rollback()
                raise HTTPException(status_code=400, detail=f"备份无效或文件无法恢复：{exc}") from exc
            except BaseException:
                await db.rollback()
                rewriter.rollback()
                raise

    return {
        "mode": mode,
        "imported": imported,
    }
