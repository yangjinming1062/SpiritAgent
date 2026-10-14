"""上传输入契约，业务用例不依赖 HTTP 框架的临时文件类。"""

from typing import BinaryIO, Protocol


class UploadSource(Protocol):
    filename: str | None
    content_type: str | None
    size: int | None
    file: BinaryIO

    async def read(self, size: int = -1) -> bytes: ...
