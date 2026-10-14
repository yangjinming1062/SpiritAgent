from collections.abc import Callable, Coroutine
from datetime import date
from typing import Any

import features.accounts.admin_operations as account_admin
import features.configuration.admin_operations as configuration_admin
import features.configuration.social_accounts as social_accounts
import workflows.maintenance.admin_operations as maintenance_admin
import workflows.nightly.log_queries as nightly_logs
from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from features.accounts.deps import get_current_admin_token
from features.accounts.schemas import (
    UserBackupImportResponse,
    UserCreate,
    UserListResponse,
    UserModelConfigListResponse,
    UserModelConfigRequest,
    UserResponse,
    UserUpdate,
)
from features.automation.schemas import NightlyActivityLogListResponse
from features.configuration.social_accounts import XiaohongshuAdminLoginStatus
from infrastructure.ai.config import ModelDiscoveryRequest
from infrastructure.ai.models import ModelDiscoveryResponse
from infrastructure.database import DbSession
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.web.xiaohongshu import XhsLoginQrCode, XhsLoginVerification
from runtime.responses import MessageResponse
from starlette.responses import Response
from workflows.maintenance.backup.serializers import BACKUP_SECTION_IDS, BackupImportMode

from entrypoints.http.auth import CurrentAdmin
from entrypoints.http.file_results import file_response
from entrypoints.http.routing import get_router

logger = get_logger(__name__)


class _AdminRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            login_management = request.url.path.startswith("/api/admin/social/xiaohongshu/login")
            try:
                response = await handler(request)
            except RequestValidationError as exc:
                # 管理请求包含密钥，校验响应只返回字段位置与原因。
                detail = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
                raise HTTPException(
                    status_code=422,
                    detail=detail,
                    headers={"Cache-Control": "no-store"} if login_management else None,
                ) from None
            except HTTPException as exc:
                if login_management:
                    exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            if login_management:
                response.headers["Cache-Control"] = "no-store"
            return response

        return handle


router = get_router(dependencies=[Depends(get_current_admin_token)])
router.route_class = _AdminRoute


@router.post("/ai/models/discover", response_model=ModelDiscoveryResponse)
async def discover_ai_models(payload: ModelDiscoveryRequest, db: DbSession) -> ModelDiscoveryResponse:
    try:
        result = await configuration_admin.discover_ai_models(payload=payload, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/users", response_model=UserListResponse)
async def list_users(db: DbSession) -> UserListResponse:
    try:
        result = await account_admin.list_users(db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.post("/users", response_model=UserResponse)
async def create_user(payload: UserCreate, db: DbSession) -> UserResponse:
    try:
        result = await account_admin.create_user(payload=payload, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, payload: UserUpdate, db: DbSession) -> UserResponse:
    try:
        result = await account_admin.update_user(user_id=user_id, payload=payload, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_user(user_id: int, db: DbSession) -> MessageResponse:
    """被遗忘权：在维护边界内停稳该用户的运行时，先删文件再删行（外键级联清理其余数据）；文件先于行删除，任一步失败都保留用户行，管理员重试即可继续清理。"""
    try:
        result = await maintenance_admin.delete_user(user_id=user_id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.patch("/users/{user_id}/toggle-active", response_model=UserResponse)
async def toggle_user_active(user_id: int, db: DbSession) -> UserResponse:
    try:
        result = await account_admin.toggle_user_active(user_id=user_id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/model-configs", response_model=UserModelConfigListResponse)
async def list_model_configs(db: DbSession) -> UserModelConfigListResponse:
    try:
        result = await configuration_admin.list_model_configs(db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/runtime-info")
async def runtime_info() -> dict[str, str]:
    """把 ``public_base_url`` 暴露给 admin 页：创建账号时自动填进激活码的 ``baseUrl``，留空时前端再降级到 ``http://localhost:10620``。"""
    try:
        result = await configuration_admin.runtime_info()
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/system-settings")
async def get_system_settings() -> dict[str, Any]:
    """获取系统全部动态配置项（敏感 Key 自动脱敏）。"""
    try:
        result = await configuration_admin.get_system_settings()
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.put("/system-settings")
async def update_system_settings(payload: dict[str, Any], db: DbSession) -> dict[str, Any]:
    """更新系统动态配置，实时持久化到数据库并热重载生效。"""
    try:
        result = await configuration_admin.update_system_settings(payload=payload, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/nightly-activity-logs", response_model=NightlyActivityLogListResponse)
async def list_nightly_activity_logs(
    db: DbSession,
    user_id: int | None = None,
    target_date: date | None = None,
) -> NightlyActivityLogListResponse:
    """查询夜间自主活动日志。可选按 user_id 和 target_date 过滤。"""
    try:
        result = await nightly_logs.list_nightly_activity_logs(db=db, user_id=user_id, target_date=target_date)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/social/xiaohongshu/login/status", response_model=XiaohongshuAdminLoginStatus)
async def xiaohongshu_login_status(
    session_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{16,96}$"),
) -> XiaohongshuAdminLoginStatus:
    try:
        return await social_accounts.login_status(session_id=session_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.post("/social/xiaohongshu/login/qrcode", response_model=XhsLoginQrCode)
async def refresh_xiaohongshu_login_qrcode() -> XhsLoginQrCode:
    try:
        return await social_accounts.refresh_login_qrcode()
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.post("/social/xiaohongshu/login/verification", response_model=XiaohongshuAdminLoginStatus)
async def submit_xiaohongshu_login_verification(payload: XhsLoginVerification) -> XiaohongshuAdminLoginStatus:
    try:
        return await social_accounts.submit_login_verification(payload)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.delete("/social/xiaohongshu/login", response_model=MessageResponse)
async def clear_xiaohongshu_saved_login() -> MessageResponse:
    try:
        return await social_accounts.clear_saved_login()
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.put("/{user_id}/model-config")
async def upsert_model_config(user_id: int, payload: UserModelConfigRequest, db: DbSession) -> MessageResponse:
    try:
        result = await configuration_admin.upsert_model_config(user_id=user_id, payload=payload, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.delete("/{user_id}/model-config")
async def delete_model_config(user_id: int, db: DbSession) -> MessageResponse:
    try:
        result = await configuration_admin.delete_model_config(user_id=user_id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/users/{user_id}/export")
async def export_user_backup(
    user_id: int,
    admin: CurrentAdmin,
    db: DbSession,
) -> FileResponse:
    """始终导出全量数据；恢复范围在导入时选择。"""
    try:
        result = await maintenance_admin.export_user_backup(user_id=user_id, admin=admin, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return file_response(result)


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
    try:
        result = await maintenance_admin.import_user_backup(
            user_id=user_id,
            db=db,
            file=file,
            mode=mode,
            sections=sections,
        )
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result
