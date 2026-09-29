from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


def log_paid_call(provider: str, kind: str, *, user_id: int | None = None, **extra: Any) -> None:
    """付费供应商调用面包屑日志：每次计费调用一行 INFO，下载或后续处理失败时仍可从日志追溯。"""
    fields: dict[str, Any] = {"provider": provider, "kind": kind, **extra}
    if user_id is not None:
        fields["user_id"] = user_id
    logger.info(f"paid call: {kind}", extra=fields)
