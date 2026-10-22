"""路由登记的公开准入、载荷与等待策略；鉴权和客户端共用同一声明。"""

from dataclasses import dataclass
from functools import lru_cache
from re import Pattern
from typing import Literal

from starlette.routing import compile_path

type HttpIdentity = Literal["public", "desktop", "remote", "admin"]
type UploadKind = Literal["json", "chat", "image", "image_file", "video", "audio", "backup", "release"]


@dataclass(frozen=True, slots=True)
class HttpOperation:
    operation_id: str
    path: str
    method: str
    identities: tuple[HttpIdentity, ...]
    upload: UploadKind = "json"

    def permits(self, identity: HttpIdentity) -> bool:
        return identity in self.identities


_operations: dict[tuple[str, str], HttpOperation] = {}
_operation_ids: dict[str, tuple[str, str]] = {}


def register_http_operation(operation: HttpOperation) -> None:
    route = operation.method, operation.path
    previous = _operation_ids.get(operation.operation_id)
    if previous is not None and previous != route:
        raise ValueError(f"Duplicate HTTP operationId: {operation.operation_id}")
    _operation_ids[operation.operation_id] = route
    _operations[route] = operation
    _route_patterns.cache_clear()


@lru_cache
def _route_patterns(method: str) -> tuple[tuple[Pattern[str], HttpOperation], ...]:
    candidates = sorted(
        (operation for operation in _operations.values() if operation.method == method),
        key=lambda operation: (operation.path.count("{"), -len(operation.path)),
    )
    return tuple((compile_path(operation.path.rstrip("/") or "/")[0], operation) for operation in candidates)


def find_http_operation(path: str, method: str) -> HttpOperation | None:
    path = path.rstrip("/") or "/"
    for pattern, operation in _route_patterns(method.upper()):
        if pattern.fullmatch(path):
            return operation
    return None


def operation_policy(
    *,
    identities: tuple[HttpIdentity, ...] | None = None,
    upload: UploadKind | None = None,
    wait_for_model: bool | None = None,
    ipc: bool | None = None,
) -> dict:
    values: dict = {}
    if identities is not None:
        values["identities"] = list(identities)
    if upload is not None:
        values["upload"] = upload
    if wait_for_model is not None:
        values["wait_for_model"] = wait_for_model
    if ipc is not None:
        values["ipc"] = ipc
    return {"x-spiritagent": values}
