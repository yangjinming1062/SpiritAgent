import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from .config import SETTINGS

# stdlib LogRecord 属性集合——导入时取，未来 Python 增加字段不会悄悄泄漏到 JSON；`color_message` 来自 uvicorn，`message`/`asctime` 由 Formatter.format() 填充，会与 `extra=` 冲突。
_RESERVED_LOGRECORD_KEYS: frozenset[str] = frozenset(
    set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "color_message"},
)

# HTTP 入口（correlation_id_middleware）与长生命周期 task tick 顶部（scheduler_loop / _process_events）显式 set_request_id() 注入；_RequestContextFilter 自动透传到每条 LogRecord。
_user_id_var: ContextVar[int | None] = ContextVar("logger_user_id", default=None)
_request_id_var: ContextVar[str | None] = ContextVar("logger_request_id", default=None)


def set_request_user_id(user_id: int | None) -> None:
    _user_id_var.set(user_id)


def set_request_id(request_id: str | None) -> None:
    _request_id_var.set(request_id)


def current_request_id() -> str | None:
    """对外 reader；优先于直接碰 `_request_id_var`。"""
    return _request_id_var.get()


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {k: v for k, v in record.__dict__.items() if k not in _RESERVED_LOGRECORD_KEYS and not k.startswith("_")}


class _RequestContextFilter(logging.Filter):
    """把 ContextVar 注入到每条 LogRecord；caller 显式 extra= 优先。"""

    def filter(self, record: logging.LogRecord) -> bool:
        uid = _user_id_var.get()
        if uid is not None and "user_id" not in record.__dict__:
            record.user_id = uid
        rid = _request_id_var.get()
        if rid is not None and "request_id" not in record.__dict__:
            record.request_id = rid
        return True


class _JsonFormatter(logging.Formatter):
    """JSON 行输出；不做脱敏——信任上游已脱敏的字符串。"""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return json.dumps(
            {
                "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "logger": record.name,
                "msg": msg,
                **_extras(record),
            },
            ensure_ascii=False,
            default=str,
        )


class _TextFormatter(logging.Formatter):
    """人类可读 fallback（LOG_FORMAT=text），extras 追加在尾部。"""

    DEFAULT_FMT = "%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.DEFAULT_FMT, datefmt="%Y-%m-%dT%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        extras = _extras(record)
        if extras:
            msg += f" {extras}"
        return msg


_FORMATTERS: dict[str, type[logging.Formatter]] = {"json": _JsonFormatter, "text": _TextFormatter}


def setup_logging() -> None:
    """lifespan 入口调一次接管 root logger；不用 dictConfig（会重新实例化 handler、丢弃我们挂的 formatter/filter）。"""
    try:
        formatter_cls = _FORMATTERS[SETTINGS.log_format]
    except KeyError:
        # pydantic Literal 已拦截，此处只对测试 monkey-patch 生效。
        raise ValueError(f"invalid log_format: {SETTINGS.log_format!r}") from None

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter_cls())
    handler.addFilter(_RequestContextFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(SETTINGS.log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


def get_logger(name: str | None = None) -> logging.Logger:
    """统一 logger 入口；传 None 时拿真正的 root logger。"""
    return logging.getLogger(name) if name is not None else logging.getLogger()
