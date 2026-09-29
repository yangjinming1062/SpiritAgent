from fastapi import HTTPException
from services.infrastructure.llm import ClassifiedError


def classified_http_exception(classified: ClassifiedError) -> HTTPException:
    """保留上游 4xx 让 renderer 按状态码分流处理；5xx、非 HTTP 或越界状态码统一归为 500。"""
    upstream = classified.status_code or 500
    return HTTPException(
        status_code=upstream if 400 <= upstream < 500 else 500,
        detail={
            "error": classified.message or classified.reason.value,
            "reason": classified.reason.value,
            "status": classified.status_code,
        },
    )


def missing_config_http(svc_label: str) -> HTTPException:
    """供应商链为空（MissingLlmConfigError）时的统一 400 响应信封。"""
    return HTTPException(
        status_code=400,
        detail={"error": f"{svc_label} provider not configured", "reason": "missing_config", "status": 400},
    )
