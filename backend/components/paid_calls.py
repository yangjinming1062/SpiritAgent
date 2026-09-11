from typing import Any, Literal

from .logger import get_logger

logger = get_logger(__name__)


def log_paid_call(
    provider: str,
    kind: str,
    *,
    task_id: str | None = None,
    user_id: int | None = None,
    level: Literal["info", "debug", "warning"] = "info",
    **extra: Any,
) -> None:
    """付费供应商（LLM / image / video / 3D）面包屑日志：task_id 类走 INFO（每计费任务一行），URL 列表等大 payload 默认 DEBUG；异步供应商 submit/result 分两条记录，下载崩了也能从日志找回 task_id + URL。"""
    fields: dict[str, Any] = {"provider": provider, "kind": kind, **extra}
    if user_id is not None:
        fields["user_id"] = user_id
    if task_id:
        fields["task_id"] = task_id
    getattr(logger, level)(f"paid call: {kind}", extra=fields)
