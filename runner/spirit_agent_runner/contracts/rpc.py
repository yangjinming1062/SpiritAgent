from typing import Any


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **({"data": self.data} if self.data else {})}
