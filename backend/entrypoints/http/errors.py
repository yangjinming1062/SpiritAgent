"""HTTP 异常、校验和中间件统一使用公开错误信封。"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from infrastructure.api_errors import ApiErrorResponse, api_error
from infrastructure.request_errors import RequestError
from pydantic import JsonValue
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


def error_response(
    status: int,
    message: str,
    *,
    code: str | None = None,
    details: JsonValue = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ApiErrorResponse(error=api_error(status, message, code=code, details=details)).model_dump(mode="json"),
        headers={"Cache-Control": "no-store", **(headers or {})},
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return error_response(exc.status_code, str(exc.detail), headers=exc.headers)


async def request_exception_handler(request: Request, exc: RequestError) -> JSONResponse:
    return error_response(exc.status_code, exc.message, code=exc.code, details=exc.details)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
    return error_response(422, "请求参数无效", details={"fields": fields})
