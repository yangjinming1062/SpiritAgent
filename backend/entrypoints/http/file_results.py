"""将文件交付结果转换为 ASGI 响应。"""

from infrastructure.async_io import run_blocking
from infrastructure.file_results import DeferredCleanup, FileResult
from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send


class _CleanupFileResponse(FileResponse):
    def __init__(self, result: FileResult) -> None:
        super().__init__(
            path=result.path,
            media_type=result.media_type,
            filename=result.filename,
            headers=result.headers,
        )
        self._cleanup: DeferredCleanup | None = result.background

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._cleanup is not None:
                await run_blocking(self._cleanup.function)


def file_response(result: FileResult) -> FileResponse:
    return _CleanupFileResponse(result)
