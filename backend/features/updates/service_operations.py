"""服务更新的管理操作：校验更新器完整响应，协议错误不伪装为空状态。"""

from collections.abc import Awaitable

from infrastructure.request_errors import RequestError
from infrastructure.updater_contracts import (
    ServiceUpdateApplyRequest,
    ServiceUpdateCheckRequest,
    ServiceUpdateLogChunk,
    ServiceUpdaterStatus,
    ServiceUpdateTaskAccepted,
    ServiceUpdateTaskList,
    ServiceUpdateTaskView,
    UpdaterStatus,
)
from infrastructure.web import service_updater
from pydantic import BaseModel, ValidationError


async def _updater_response[T: BaseModel](request: Awaitable[dict], model: type[T]) -> T:
    try:
        return model.model_validate(await request)
    except ValidationError as exc:
        raise RequestError(status_code=502, message="更新器返回的数据不符合当前协议，请检查更新器版本与状态。") from exc


async def service_updater_status() -> ServiceUpdaterStatus:
    if not service_updater.updater_enabled():
        return ServiceUpdaterStatus(enabled=False)
    status = await _updater_response(service_updater.updater_status(), UpdaterStatus)
    return ServiceUpdaterStatus(enabled=True, **status.model_dump())


async def start_service_update_check(payload: ServiceUpdateCheckRequest) -> ServiceUpdateTaskAccepted:
    return await _updater_response(service_updater.start_check(payload.request_id), ServiceUpdateTaskAccepted)


async def apply_service_update(payload: ServiceUpdateApplyRequest) -> ServiceUpdateTaskAccepted:
    return await _updater_response(
        service_updater.start_update(payload.request_id, payload.target_commit.lower()), ServiceUpdateTaskAccepted
    )


async def service_update_task(task_id: str) -> ServiceUpdateTaskView:
    return await _updater_response(service_updater.task_view(task_id), ServiceUpdateTaskView)


async def service_update_task_log(task_id: str, offset: int) -> ServiceUpdateLogChunk:
    return await _updater_response(service_updater.task_log(task_id, offset), ServiceUpdateLogChunk)


async def service_update_task_list() -> ServiceUpdateTaskList:
    return await _updater_response(service_updater.task_list(), ServiceUpdateTaskList)
