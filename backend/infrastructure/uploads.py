"""上传输入契约，业务用例不依赖 HTTP 框架的临时文件类。"""

from typing import BinaryIO, Protocol

from infrastructure.request_errors import RequestError


class UploadSource(Protocol):
    filename: str | None
    content_type: str | None
    size: int | None
    file: BinaryIO

    async def read(self, size: int = -1) -> bytes: ...


async def read_capped_upload(file: UploadSource, max_bytes: int, too_large: RequestError) -> bytes:
    """先核对上传实测大小，读取仍限制长度，不信任缺失或偏小的元数据。"""
    if file.size is not None and file.size > max_bytes:
        raise too_large
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise too_large
    return data
