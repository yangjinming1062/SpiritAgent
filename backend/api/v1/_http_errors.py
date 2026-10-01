from components import get_logger
from fastapi import HTTPException
from services.infrastructure.llm import ClassifiedError, classify_api_error

logger = get_logger(__name__)


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


def llm_http_error(e: Exception, op: str) -> HTTPException:
    """分类上游供应商错误并返回非泄露错误信封；原始异常与 traceback 只留在服务端日志。"""
    classified = classify_api_error(e)
    logger.warning(
        "provider operation failed",
        extra={
            "operation": op,
            "reason": classified.reason.value,
            "status_code": classified.status_code,
            "error": str(e),
        },
        exc_info=True,
    )
    return classified_http_exception(classified)


def missing_config_http(service: str = "生成服务", *, action: str = "请联系管理员") -> HTTPException:
    """供应商链为空（MissingLlmConfigError）时的统一 400 响应信封；能力链由管理员维护，桌面端无法自行配置。"""
    return HTTPException(
        status_code=400,
        detail={"error": f"{service}暂未配置，{action}", "reason": "missing_config", "status": 400},
    )
