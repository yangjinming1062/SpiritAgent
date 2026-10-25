from typing import Annotated

import features.updates.operations as updates
import features.updates.service_operations as service_updates
from fastapi import File, Form, Path, Query, UploadFile
from fastapi.responses import FileResponse
from features.updates.release_contracts import ReleaseManifestResponse
from features.updates.schemas import UpdateVersionItem, UpdateVersionListResponse, UpdateVersionUpdate
from features.updates.service_schemas import (
    ServiceUpdateApplyRequest,
    ServiceUpdateCheckRequest,
    ServiceUpdateLogChunk,
    ServiceUpdaterStatus,
    ServiceUpdateTaskAccepted,
    ServiceUpdateTaskList,
    ServiceUpdateTaskView,
)
from infrastructure.api_ids import ResourceId
from infrastructure.constants import POSTGRES_INT8_MAX
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy
from runtime.responses import MessageResponse

from entrypoints.http.auth import CurrentAdmin
from entrypoints.http.file_results import file_response
from entrypoints.http.routing import get_router

router = get_router(identity="public")
management_router = get_router(prefix="/api/admin/updates", identity="admin")

# 任务号在路径与日志查询中保持同一约束。
TaskId = Annotated[str, Path(pattern=r"^[0-9A-Za-z_-]{6,32}$")]


@router.get("/latest.yml", response_model=ReleaseManifestResponse)
async def get_latest_yml(db: DbSession) -> ReleaseManifestResponse:
    return await updates.get_latest_yml(db=db)


@router.get("/latest-mac.yml", response_model=ReleaseManifestResponse)
async def get_latest_mac_yml(db: DbSession) -> ReleaseManifestResponse:
    return await updates.get_latest_mac_yml(db=db)


@router.get("/latest-runner.yml", response_class=FileResponse)
async def get_latest_runner_yml(db: DbSession) -> FileResponse:
    """原样下发构建时签名的 Runner wheel 清单。"""
    result = await updates.get_latest_runner_yml(db=db)
    return file_response(result)


@management_router.get(
    "/versions", response_model=UpdateVersionListResponse, openapi_extra=operation_policy(identities=("admin",))
)
async def list_versions(_admin: CurrentAdmin, db: DbSession) -> UpdateVersionListResponse:
    return await updates.list_versions(_admin=_admin, db=db)


@management_router.post(
    "/versions",
    response_model=UpdateVersionItem,
    status_code=201,
    openapi_extra=operation_policy(identities=("admin",), upload="release"),
)
async def create_version(
    admin: CurrentAdmin,
    db: DbSession,
    file: UploadFile = File(...),
    release_notes: Annotated[str, Form(pattern=r"^[^\x00]*$")] = "",
) -> UpdateVersionItem:
    # Build-UpdateZip 产出的更新包，必须含 Windows 安装程序（*.exe）。
    return await updates.create_version(admin=admin, db=db, file=file, release_notes=release_notes)


@management_router.patch(
    "/versions/{id}", response_model=UpdateVersionItem, openapi_extra=operation_policy(identities=("admin",))
)
async def update_version(
    id: Annotated[ResourceId, Path(ge=1, le=POSTGRES_INT8_MAX)],
    payload: UpdateVersionUpdate,
    _admin: CurrentAdmin,
    db: DbSession,
) -> UpdateVersionItem:
    return await updates.update_version(id=id, payload=payload, _admin=_admin, db=db)


@management_router.delete(
    "/versions/{id}", response_model=MessageResponse, openapi_extra=operation_policy(identities=("admin",))
)
async def delete_version(
    id: Annotated[ResourceId, Path(ge=1, le=POSTGRES_INT8_MAX)], _admin: CurrentAdmin, db: DbSession
) -> MessageResponse:
    return await updates.delete_version(id=id, _admin=_admin, db=db)


@router.get("/{filename:path}", response_class=FileResponse)
async def get_latest_file(filename: str, db: DbSession) -> FileResponse:
    result = await updates.get_latest_file(filename=filename, db=db)
    return file_response(result)


@management_router.get(
    "/service/status", response_model=ServiceUpdaterStatus, openapi_extra=operation_policy(identities=("admin",))
)
async def get_service_updater_status(_admin: CurrentAdmin) -> ServiceUpdaterStatus:
    return await service_updates.service_updater_status()


@management_router.post(
    "/service/check", response_model=ServiceUpdateTaskAccepted, openapi_extra=operation_policy(identities=("admin",))
)
async def start_service_update_check(
    payload: ServiceUpdateCheckRequest, _admin: CurrentAdmin
) -> ServiceUpdateTaskAccepted:
    return await service_updates.start_service_update_check(payload)


@management_router.post(
    "/service/apply", response_model=ServiceUpdateTaskAccepted, openapi_extra=operation_policy(identities=("admin",))
)
async def apply_service_update(payload: ServiceUpdateApplyRequest, _admin: CurrentAdmin) -> ServiceUpdateTaskAccepted:
    return await service_updates.apply_service_update(payload)


@management_router.get(
    "/service/tasks", response_model=ServiceUpdateTaskList, openapi_extra=operation_policy(identities=("admin",))
)
async def list_service_update_tasks(_admin: CurrentAdmin) -> ServiceUpdateTaskList:
    return await service_updates.service_update_task_list()


@management_router.get(
    "/service/tasks/{task_id}",
    response_model=ServiceUpdateTaskView,
    openapi_extra=operation_policy(identities=("admin",)),
)
async def get_service_update_task(task_id: TaskId, _admin: CurrentAdmin) -> ServiceUpdateTaskView:
    return await service_updates.service_update_task(task_id)


@management_router.get(
    "/service/tasks/{task_id}/log",
    response_model=ServiceUpdateLogChunk,
    openapi_extra=operation_policy(identities=("admin",)),
)
async def get_service_update_task_log(
    task_id: TaskId, _admin: CurrentAdmin, offset: Annotated[int, Query(ge=0)] = 0
) -> ServiceUpdateLogChunk:
    return await service_updates.service_update_task_log(task_id, offset)
