import asyncio
import functools
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, Request, Response
from starlette.responses import StreamingResponse

from .asset_store import compute_file_sha256

_RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK_SIZE = 256 * 1024  # 256 KB


@functools.lru_cache(maxsize=1000)
def _cached_sha256(path: str, _mtime: float, _size: int) -> str:
    return compute_file_sha256(path)


def _get_file_sha256(file_path: Path) -> str:
    """按 (路径, mtime, 大小) 缓存内容哈希，文件被替换后自然失效；在工作线程并发调用，缓存须线程安全。"""
    st = file_path.stat()
    return _cached_sha256(str(file_path.resolve()), st.st_mtime, st.st_size)


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int] | None:
    """解析 Range 头（bytes=0-499 / 500- / -500），返回闭区间 (start, end)，非法或不可满足时返回 None。"""
    match = _RANGE_PATTERN.match(range_header.strip())
    if not match:
        return None

    raw_start, raw_end = match.groups()

    try:
        if raw_start and raw_end:
            start = int(raw_start)
            end = int(raw_end)
            if start > end or start >= file_size:
                return None
            return start, min(end, file_size - 1)

        if raw_start and not raw_end:
            start = int(raw_start)
            if start >= file_size:
                return None
            return start, file_size - 1

        if not raw_start and raw_end:
            suffix = int(raw_end)
            if suffix <= 0:
                return None
            start = max(0, file_size - suffix)
            return start, file_size - 1
    except ValueError:
        # Python rejects excessively long decimal strings before ``int`` can produce a value.
        return None

    return None


async def _iter_file(file_path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    def _open_and_seek() -> BinaryIO:
        fh = open(file_path, "rb")  # noqa: SIM115
        fh.seek(start)
        return fh

    f = await asyncio.to_thread(_open_and_seek)
    try:
        remaining = length
        while remaining > 0:
            chunk = await asyncio.to_thread(f.read, min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await asyncio.to_thread(f.close)


async def serve_ranged_file(request: Request, file_path: Path, media_type: str) -> Response:
    """以流式方式下发文件，支持 Range(206/416)、ETag 与不可变缓存头，避免大模型文件整体进内存。"""
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    file_size = file_path.stat().st_size
    sha256 = await asyncio.to_thread(_get_file_sha256, file_path)
    etag = f'"{sha256}"'

    base_headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        # 响应需鉴权或短时签名，只允许客户端私有缓存，禁止共享缓存跨用户或越过签名有效期复用
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Sha256": sha256,
    }

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip() in (etag, sha256, "*"):
        return Response(status_code=304, headers=base_headers)

    range_header = request.headers.get("range")
    if range_header and "," in range_header:
        # 多 Range（bytes=0-1,3-4）语法合法但不支持：按 RFC 7233 忽略该头，整体 200 返回
        range_header = None

    if not range_header:
        headers = {**base_headers, "Content-Length": str(file_size)}
        return StreamingResponse(
            _iter_file(file_path, 0, file_size),
            status_code=200,
            media_type=media_type,
            headers=headers,
        )

    range_bounds = _parse_range_header(range_header, file_size)
    if range_bounds is None:
        return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{file_size}"})

    start, end = range_bounds
    chunk_length = end - start + 1
    ranged_headers = {
        **base_headers,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(chunk_length),
    }

    return StreamingResponse(
        _iter_file(file_path, start, chunk_length),
        status_code=206,
        media_type=media_type,
        headers=ranged_headers,
    )
