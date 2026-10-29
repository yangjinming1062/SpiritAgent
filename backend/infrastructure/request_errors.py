"""框架无关的请求失败；公开状态、机器码和细节由传输边界序列化。"""

from pydantic import JsonValue


class RequestError(Exception):
    def __init__(self, status_code: int, message: str, *, code: str | None = None, details: JsonValue = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.details = details
