from collections.abc import Callable, Coroutine
from datetime import date
from typing import Annotated, Any

import features.accounts.admin_operations as account_admin
import features.configuration.admin_operations as configuration_admin
import features.configuration.social_accounts as social_accounts
import workflows.maintenance.admin_operations as maintenance_admin
import workflows.nightly.log_queries as nightly_logs
from fastapi import Depends, File, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from features.accounts.deps import get_current_admin_token
from features.accounts.schemas import (
    RegenerateActivationRequest,
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
from features.media.video_queue import admin as video_queue_admin
from features.media.video_queue.contracts import VideoQueueList, VideoQueueRequestView, VideoQueueResolution
from infrastructure.ai.config import ModelDiscoveryRequest
from infrastructure.ai.models import ModelDiscoveryResponse
from infrastructure.api_ids import ResourceId
from infrastructure.backup import BackupImportMode
from infrastructure.constants import POSTGRES_INT8_MAX
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy
from infrastructure.web.xiaohongshu import XhsLoginQrCode, XhsLoginVerification
from pydantic import JsonValue
from runtime.responses import MessageResponse
from starlette.responses import Response
from workflows.maintenance.backup.serializers import BACKUP_SECTION_IDS

from entrypoints.http.auth import CurrentAdmin
from entrypoints.http.file_results import file_response
from entrypoints.http.routing import get_router


class _AdminRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            login_management = request.url.path.startswith("/api/admin/social/xiaohongshu/login")
            response = await handler(request)
            if login_management:
                response.headers["Cache-Control"] = "no-store"
            return response

        return handle


router = get_router(dependencies=[Depends(get_current_admin_token)], identity="admin")
router.route_class = _AdminRoute
QueueToken = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


@router.get("/video-queue", response_model=VideoQueueList)
async def video_queue(db: DbSession) -> VideoQueueList:
    return await video_queue_admin.list_video_queue(db)


@router.post("/video-queue/{request_id}/resolve", response_model=VideoQueueRequestView)
async def reconcile_video_queue(
    request_id: QueueToken, payload: VideoQueueResolution, admin: CurrentAdmin, db: DbSession
) -> VideoQueueRequestView:
    return await video_queue_admin.resolve_video_queue(db, request_id, payload, admin)


@router.post("/video-queue/{request_id}/cancel", response_model=MessageResponse)
async def cancel_video_queue_request(request_id: QueueToken, admin: CurrentAdmin) -> MessageResponse:
    return await video_queue_admin.cancel_video_queue_request(request_id, admin)


@router.post("/video-queue/lanes/{lane_id}/retry", response_model=MessageResponse)
async def retry_video_queue_lane(lane_id: QueueToken, admin: CurrentAdmin, db: DbSession) -> MessageResponse:
    return await video_queue_admin.retry_video_queue_lane(db, lane_id, admin)


@router.post("/ai/models/discover", response_model=ModelDiscoveryResponse)
async def discover_ai_models(payload: ModelDiscoveryRequest, db: DbSession) -> ModelDiscoveryResponse:
    return await configuration_admin.discover_ai_models(payload=payload, db=db)


@router.get("/users", response_model=UserListResponse)
async def list_users(db: DbSession) -> UserListResponse:
    return await account_admin.list_users(db=db)


@router.post("/users", response_model=UserResponse)
async def create_user(payload: UserCreate, db: DbSession) -> UserResponse:
    return await account_admin.create_user(payload=payload, db=db)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: ResourceId, payload: UserUpdate, db: DbSession) -> UserResponse:
    return await account_admin.update_user(user_id=user_id, payload=payload, db=db)


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_user(user_id: ResourceId, db: DbSession) -> MessageResponse:
    """被遗忘权：在维护边界内停稳该用户的运行时，先删文件再删行（外键级联清理其余数据）；文件先于行删除，任一步失败都保留用户行，管理员重试即可继续清理。"""
    return await maintenance_admin.delete_user(user_id=user_id, db=db)


@router.post("/users/{user_id}/activation", response_model=UserResponse)
async def regenerate_activation(
    user_id: ResourceId, payload: RegenerateActivationRequest, db: DbSession
) -> UserResponse:
    return await account_admin.regenerate_activation(user_id, payload, db)


@router.get("/model-configs", response_model=UserModelConfigListResponse)
async def list_model_configs(db: DbSession) -> UserModelConfigListResponse:
    return await configuration_admin.list_model_configs(db=db)


@router.get("/runtime-info")
async def runtime_info() -> dict[str, str]:
    """把 ``public_base_url`` 暴露给 admin 页：创建账号时自动填进激活码的 ``baseUrl``，留空时前端再降级到 ``http://localhost:10620``。"""
    return await configuration_admin.runtime_info()


@router.get("/system-settings")
async def get_system_settings() -> dict[str, JsonValue]:
    """获取系统全部动态配置项（敏感 Key 自动脱敏）。"""
    return await configuration_admin.get_system_settings()


@router.put("/system-settings")
async def update_system_settings(payload: dict[str, JsonValue], db: DbSession) -> dict[str, JsonValue]:
    """更新系统动态配置，实时持久化到数据库并热重载生效。"""
    return await configuration_admin.update_system_settings(payload=payload, db=db)


@router.get("/nightly-activity-logs", response_model=NightlyActivityLogListResponse)
async def list_nightly_activity_logs(
    db: DbSession,
    user_id: Annotated[ResourceId | None, Query(ge=1, le=POSTGRES_INT8_MAX)] = None,
    target_date: date | None = None,
) -> NightlyActivityLogListResponse:
    """查询夜间自主活动日志。可选按 user_id 和 target_date 过滤。"""
    return await nightly_logs.list_nightly_activity_logs(db=db, user_id=user_id, target_date=target_date)


@router.get("/social/xiaohongshu/login/status", response_model=XiaohongshuAdminLoginStatus)
async def xiaohongshu_login_status(
    session_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{16,96}$"),
) -> XiaohongshuAdminLoginStatus:
    return await social_accounts.login_status(session_id=session_id)


@router.post("/social/xiaohongshu/login/qrcode", response_model=XhsLoginQrCode)
async def refresh_xiaohongshu_login_qrcode() -> XhsLoginQrCode:
    return await social_accounts.refresh_login_qrcode()


@router.post("/social/xiaohongshu/login/verification", response_model=XiaohongshuAdminLoginStatus)
async def submit_xiaohongshu_login_verification(payload: XhsLoginVerification) -> XiaohongshuAdminLoginStatus:
    return await social_accounts.submit_login_verification(payload)


@router.delete("/social/xiaohongshu/login", response_model=MessageResponse)
async def clear_xiaohongshu_saved_login() -> MessageResponse:
    return await social_accounts.clear_saved_login()


@router.put("/users/{user_id}/model-config")
async def upsert_model_config(user_id: ResourceId, payload: UserModelConfigRequest, db: DbSession) -> MessageResponse:
    return await configuration_admin.upsert_model_config(user_id=user_id, payload=payload, db=db)


@router.delete("/users/{user_id}/model-config")
async def delete_model_config(user_id: ResourceId, db: DbSession) -> MessageResponse:
    return await configuration_admin.delete_model_config(user_id=user_id, db=db)


@router.get("/users/{user_id}/export")
async def export_user_backup(user_id: ResourceId, admin: CurrentAdmin, db: DbSession) -> FileResponse:
    """始终导出全量数据；恢复范围在导入时选择。"""
    result = await maintenance_admin.export_user_backup(user_id=user_id, admin=admin, db=db)
    return file_response(result)


@router.post(
    "/users/{user_id}/import", response_model=UserBackupImportResponse, openapi_extra=operation_policy(upload="backup")
)
async def import_user_backup(
    user_id: ResourceId,
    db: DbSession,
    file: UploadFile = File(...),
    mode: BackupImportMode = "overwrite",
    sections: list[str] | None = Query(
        default=None,
        description=f"恢复范围分组，可选 {', '.join(BACKUP_SECTION_IDS)} 或 all；缺省为全部",
    ),
) -> UserBackupImportResponse:
    """按所选分组恢复备份，覆盖仅清理通过当前结构和关联校验的数据类别。"""
    return await maintenance_admin.import_user_backup(
        user_id=user_id,
        db=db,
        file=file,
        mode=mode,
        sections=sections,
    )
