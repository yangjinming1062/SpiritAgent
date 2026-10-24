"""服务更新的管理操作：转发到更新器容器并校验响应。

更新器与 Backend 可各自升级，因此按字段宽松解析：列表跳过无效成员，单任务接口才严格校验。
"""

from infrastructure.request_errors import RequestError
from infrastructure.web import service_updater
from pydantic import BaseModel

from features.updates.service_schemas import (
    ServicePreflightIssue,
    ServiceRepoView,
    ServiceRuntimeView,
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


def _parse[ModelT: BaseModel](model: type[ModelT], raw: object) -> ModelT | None:
    try:
        return model.model_validate(raw)
    except ValueError:
        return None


def _parse_list[ModelT: BaseModel](model: type[ModelT], raw: object) -> list[ModelT]:
    if not isinstance(raw, list):
        return []
    return [item for item in (_parse(model, entry) for entry in raw if isinstance(entry, dict)) if item is not None]


def _parse_task(raw: object) -> ServiceUpdateTaskView:
    task = _parse(ServiceUpdateTaskView, raw)
    if task is None:
        raise RequestError(status_code=502, message="更新器返回的任务数据无效。")
    return task


def _accepted(raw: dict) -> ServiceUpdateTaskAccepted:
    return ServiceUpdateTaskAccepted(task_id=str(raw.get("task_id") or ""), existing=bool(raw.get("existing")))


async def service_updater_status() -> ServiceUpdaterStatus:
    if not service_updater.updater_enabled():
        return ServiceUpdaterStatus(enabled=False)
    try:
        raw = await service_updater.updater_status()
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return ServiceUpdaterStatus(
        enabled=True,
        updater_ready=bool(raw.get("updater_ready")),
        version=str(raw.get("version") or ""),
        repo=_parse(ServiceRepoView, raw.get("repo")) or ServiceRepoView(),
        runtime=_parse(ServiceRuntimeView, raw.get("runtime")),
        preflight_issues=_parse_list(ServicePreflightIssue, raw.get("preflight_issues")),
        active_task=_parse(ServiceUpdateTaskView, raw.get("active_task")),
        latest_task=_parse(ServiceUpdateTaskView, raw.get("latest_task")),
    )


async def start_service_update_check(payload: ServiceUpdateCheckRequest) -> ServiceUpdateTaskAccepted:
    try:
        raw = await service_updater.start_check(payload.request_id)
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return _accepted(raw)


async def apply_service_update(payload: ServiceUpdateApplyRequest) -> ServiceUpdateTaskAccepted:
    try:
        raw = await service_updater.start_update(payload.request_id, payload.target_commit.lower())
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return _accepted(raw)


async def service_update_task(task_id: str) -> ServiceUpdateTaskView:
    try:
        raw = await service_updater.task_view(task_id)
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return _parse_task(raw)


async def service_update_task_log(task_id: str, offset: int) -> ServiceUpdateLogChunk:
    try:
        raw = await service_updater.task_log(task_id, offset)
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    chunk = _parse(ServiceUpdateLogChunk, raw)
    if chunk is None:
        raise RequestError(status_code=502, message="更新器返回的日志数据无效。")
    return chunk


async def service_update_task_list() -> ServiceUpdateTaskList:
    try:
        raw = await service_updater.task_list()
    except service_updater.ServiceUpdaterError as exc:
        raise _wrap_updater_failure(exc) from None
    return ServiceUpdateTaskList(tasks=_parse_list(ServiceUpdateTaskView, raw.get("tasks")))
