"""将文件交付结果转换为 ASGI 响应。"""

from infrastructure.file_results import FileResult
from starlette.background import BackgroundTask
from starlette.responses import FileResponse


def file_response(result: FileResult) -> FileResponse:
    background = BackgroundTask(result.background.function) if result.background is not None else None
    return FileResponse(
        path=result.path,
        media_type=result.media_type,
        filename=result.filename,
        headers=result.headers,
        background=background,
    )
