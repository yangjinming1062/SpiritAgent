from typing import Annotated

import features.updates.service_operations as service_updates
from fastapi import Path, Query
from infrastructure.http_contracts import operation_policy
from infrastructure.updater_contracts import (
    ServiceUpdateApplyRequest,
    ServiceUpdateCheckRequest,
    ServiceUpdateLogChunk,
    ServiceUpdaterStatus,
    ServiceUpdateTaskAccepted,
    ServiceUpdateTaskList,
    ServiceUpdateTaskView,
)

from entrypoints.http.auth import CurrentAdmin
from entrypoints.http.routing import get_router

management_router = get_router(prefix="/api/admin/updates", identity="admin")

# 任务号在路径与日志查询中保持同一约束。
TaskId = Annotated[str, Path(pattern=r"^[0-9A-Za-z_-]{6,32}$")]


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
