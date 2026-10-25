"""服务更新的管理操作：校验更新器完整响应，协议错误不伪装为空状态。"""

from infrastructure.request_errors import RequestError
from infrastructure.web import service_updater
from pydantic import BaseModel, ValidationError

from features.updates.service_schemas import (
    ServiceUpdateApplyRequest,
    ServiceUpdateCheckRequest,
    ServiceUpdateLogChunk,
    ServiceUpdaterStatus,
    ServiceUpdateTaskAccepted,
    ServiceUpdateTaskList,
    ServiceUpdateTaskView,
)


def _wrap_updater_failure(error: service_updater.ServiceUpdaterError) -> RequestError:
    return RequestError(status_code=409 if error.status_code == 409 else 502, message=str(error))


def _parse[ModelT: BaseModel](model: type[ModelT], raw: object) -> ModelT:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise RequestError(status_code=502, message="更新器返回的数据不符合当前协议，请检查更新器版本与状态。") from exc


async def service_updater_status() -> ServiceUpdaterStatus:
    if not service_updater.updater_enabled():
        return ServiceUpdaterStatus(enabled=False)
    try:
        raw = await service_updater.updater_status()
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return _parse(ServiceUpdaterStatus, {**raw, "enabled": True})


async def start_service_update_check(payload: ServiceUpdateCheckRequest) -> ServiceUpdateTaskAccepted:
    try:
        raw = await service_updater.start_check(payload.request_id)
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return _parse(ServiceUpdateTaskAccepted, raw)


async def apply_service_update(payload: ServiceUpdateApplyRequest) -> ServiceUpdateTaskAccepted:
    try:
        raw = await service_updater.start_update(payload.request_id, payload.target_commit.lower())
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return _parse(ServiceUpdateTaskAccepted, raw)


async def service_update_task(task_id: str) -> ServiceUpdateTaskView:
    try:
        raw = await service_updater.task_view(task_id)
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return _parse(ServiceUpdateTaskView, raw)


async def service_update_task_log(task_id: str, offset: int) -> ServiceUpdateLogChunk:
    try:
        raw = await service_updater.task_log(task_id, offset)
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return _parse(ServiceUpdateLogChunk, raw)


async def service_update_task_list() -> ServiceUpdateTaskList:
    try:
        raw = await service_updater.task_list()
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return _parse(ServiceUpdateTaskList, raw)
