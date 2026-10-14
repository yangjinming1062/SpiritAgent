"""框架无关的请求失败；入口保留对外状态码和错误载荷。"""

from pydantic import JsonValue


class RequestError(Exception):
    def __init__(self, *, status_code: int, detail: JsonValue) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail
