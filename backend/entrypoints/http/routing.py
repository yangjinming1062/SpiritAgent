import sys
from collections.abc import Callable
from inspect import isclass, signature
from typing import Any

from fastapi import APIRouter
from fastapi.routing import APIRoute
from infrastructure.api_errors import ApiErrorResponse
from infrastructure.http_contracts import HttpIdentity, HttpOperation, register_http_operation
from starlette.responses import Response


class ContractRouter(APIRouter):
    """注册时固定操作策略，公开 OpenAPI 与实际准入使用相同元数据。"""

    def __init__(self, *, identities: tuple[HttpIdentity, ...], **kwargs: Any) -> None:
        self.identities = identities
        super().__init__(**kwargs)

    def add_api_route(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        operation_id = kwargs.get("operation_id") or f"{endpoint.__module__.rsplit('.', 1)[-1]}.{endpoint.__name__}"
        kwargs["operation_id"] = operation_id
        responses = dict(kwargs.get("responses") or {})
        response_type = signature(endpoint).return_annotation
        if isclass(response_type) and issubclass(response_type, Response):
            kwargs["response_class"] = response_type
            status = kwargs.get("status_code") or 200
            if status != 204:
                responses.setdefault(
                    status,
                    {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
                )
        for status in (400, 401, 403, 404, 409, 413, 422, 429, 500, 503):
            responses.setdefault(status, {"model": ApiErrorResponse})
        kwargs["responses"] = responses
        extra = dict(kwargs.get("openapi_extra") or {})
        overrides = extra.get("x-spiritagent", {})
        identities = tuple(overrides.get("identities", self.identities))
        policy = {
            "identities": list(identities),
            "upload": overrides.get("upload", "json"),
            "wait_for_model": overrides.get("wait_for_model", False),
            "ipc": overrides.get("ipc", "desktop" in identities),
        }
        extra["x-spiritagent"] = policy
        kwargs["openapi_extra"] = extra
        super().add_api_route(path, endpoint, **kwargs)
        route = self.routes[-1]
        if (
            isinstance(route, APIRoute)
            and route.body_field is not None
            and getattr(route.body_field.field_info, "media_type", None) == "multipart/form-data"
        ):
            policy["ipc"] = False
            route.openapi_extra = {**(route.openapi_extra or {}), "x-spiritagent": policy}
        for method in kwargs.get("methods") or ["GET"]:
            register_http_operation(
                HttpOperation(
                    operation_id=operation_id,
                    path=self.prefix + path,
                    method=method.upper(),
                    identities=identities,
                    upload=policy["upload"],
                ),
            )


def get_router(
    *,
    prefix: str | None = None,
    tag: str | None = None,
    dependencies: list | None = None,
    remote: bool = False,
    identity: HttpIdentity = "desktop",
) -> APIRouter:
    """默认 prefix 由 caller 模块的 leaf 名推导为 ``/api/<resource>``；传 ``prefix=""`` 挂载到根（admin 页面 router 用）。"""
    resource = sys._getframe(1).f_globals["__name__"].rsplit(".", 1)[-1]
    identities: tuple[HttpIdentity, ...] = (identity, "remote") if remote else (identity,)
    return ContractRouter(
        identities=identities,
        prefix=f"/api/{resource}" if prefix is None else prefix,
        tags=[tag or resource],
        dependencies=dependencies,
    )
