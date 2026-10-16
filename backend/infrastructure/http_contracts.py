"""路由登记的公开准入、载荷与等待策略；鉴权和客户端共用同一声明。"""

from dataclasses import dataclass
from typing import Literal

from starlette.routing import compile_path

type HttpIdentity = Literal["public", "desktop", "remote", "admin"]
type UploadKind = Literal["json", "image", "image_file", "video", "audio", "action_pack", "backup", "release"]


@dataclass(frozen=True, slots=True)
class HttpOperation:
    path: str
    method: str
    identities: tuple[HttpIdentity, ...]
    upload: UploadKind = "json"

    def permits(self, identity: HttpIdentity) -> bool:
        return identity in self.identities


_operations: dict[tuple[str, str], HttpOperation] = {}


def register_http_operation(operation: HttpOperation) -> None:
    _operations[operation.method, operation.path] = operation


def find_http_operation(path: str, method: str) -> HttpOperation | None:
    path = path.rstrip("/") or "/"
    candidates = [operation for operation in _operations.values() if operation.method == method.upper()]
    candidates.sort(key=lambda operation: (operation.path.count("{"), -len(operation.path)))
    for operation in candidates:
        pattern, _, _ = compile_path(operation.path.rstrip("/") or "/")
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
