"""Backend → 更新器容器的 HTTP 客户端；仅经共享 Unix socket 访问，不暴露网络面。"""

import asyncio
from pathlib import Path
from typing import Any

import httpx

from infrastructure.config import SETTINGS
from infrastructure.request_errors import RequestError

_TIMEOUT = httpx.Timeout(15, connect=5, read=12)
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ServiceUpdaterError(RuntimeError):
    """更新器返回非 2xx 或响应不可解析；message 面向管理员。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _socket_path() -> Path:
    return Path(SETTINGS.service_updater_socket_dir) / "updater.sock"


def updater_enabled() -> bool:
    """socket 由更新器容器创建：未启用 profile 时目录存在但为空，按未接入处理。"""

    return bool(SETTINGS.service_updater_socket_dir) and _socket_path().exists()


async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not updater_enabled():
        raise RequestError(status_code=409, message="服务更新器未启用，请先按部署文档接入更新容器。")
    transport = httpx.AsyncHTTPTransport(uds=str(_socket_path()))
    try:
        async with asyncio.timeout(20):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://updater.local", timeout=_TIMEOUT
            ) as client:
                response = await client.request(method, path, params=params, json=payload)
    except (httpx.HTTPError, TimeoutError, OSError, AttributeError):
        # AttributeError：非 Unix 平台没有 AF_UNIX，仅本地开发会遇到。
        raise ServiceUpdaterError("无法连接更新器，请检查更新容器状态与共享 socket。") from None
    if response.status_code == 404:
        raise ServiceUpdaterError("更新器接口不存在，请确认更新容器与 Backend 版本匹配。", status_code=404)
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise ServiceUpdaterError("更新器响应过大。")
    try:
        data = response.json()
    except ValueError:
        raise ServiceUpdaterError("更新器返回的响应无法解析。") from None
    if response.status_code >= 400:
        message = data.get("message") if isinstance(data, dict) else None
        raise ServiceUpdaterError(
            str(message or f"更新器请求失败（{response.status_code}）。"), status_code=response.status_code
        )
    if not isinstance(data, dict):
        raise ServiceUpdaterError("更新器返回的数据结构无效。")
    return data


async def updater_status() -> dict[str, Any]:
    return await _request("GET", "/status")


async def start_check(request_id: str) -> dict[str, Any]:
    return await _request("POST", "/tasks/check", payload={"request_id": request_id})


async def start_update(request_id: str, target_commit: str) -> dict[str, Any]:
    return await _request("POST", "/tasks/update", payload={"request_id": request_id, "target_commit": target_commit})


async def task_view(task_id: str) -> dict[str, Any]:
    return await _request("GET", "/task", params={"task_id": task_id})


async def task_log(task_id: str, offset: int) -> dict[str, Any]:
    return await _request("GET", "/log", params={"task_id": task_id, "offset": max(0, offset)})


async def task_list() -> dict[str, Any]:
    return await _request("GET", "/tasks")
