from fastapi import HTTPException
from services.infrastructure.llm import ClassifiedError


def classified_http_exception(classified: ClassifiedError) -> HTTPException:
    """保留上游 4xx 让 renderer 按状态码分流处理，仅把 5xx / 越界状态归一为 500。"""
    upstream = classified.status_code or 500
    if upstream >= 500:
        http_status = 500
    elif upstream < 400:
        # 非 HTTP 或状态码越界，统一视为服务端错误
        http_status = 500
    else:
        http_status = upstream
    return HTTPException(
        status_code=http_status,
        detail={
            "error": classified.message or classified.reason.value,
            "reason": classified.reason.value,
            "status": classified.status_code,
        },
    )


def missing_config_http(svc_label: str, status_code: int = 400) -> HTTPException:
    """链路解析器抛 MissingLlmConfigError 时的统一 400/501 响应信封。"""
    return HTTPException(
        status_code=status_code,
        detail={"error": f"{svc_label} provider not configured", "reason": "missing_config", "status": status_code},
    )
