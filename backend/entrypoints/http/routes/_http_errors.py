from infrastructure.ai.error_classifier import ClassifiedError, classify_api_error
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError

logger = get_logger(__name__)


def classified_request_error(classified: ClassifiedError) -> RequestError:
    """保留上游 4xx；其他供应商失败统一映射 500。"""
    upstream = classified.status_code or 500
    return RequestError(
        status_code=upstream if 400 <= upstream < 500 else 500,
        message=classified.message or classified.reason.value,
        code=classified.reason.value,
        details={"upstream_status": classified.status_code},
    )


def provider_request_error(error: Exception, operation: str) -> RequestError:
    classified = classify_api_error(error)
    logger.warning(
        "provider operation failed",
        extra={
            "operation": operation,
            "reason": classified.reason.value,
            "status_code": classified.status_code,
            "error": str(error),
        },
        exc_info=True,
    )
    return classified_request_error(classified)


def missing_config_error(service: str = "生成服务", *, action: str = "请联系管理员") -> RequestError:
    return RequestError(status_code=400, message=f"{service}暂未配置，{action}", code="missing_config")
