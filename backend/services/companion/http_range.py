import asyncio
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import HTTPException, Request, Response
from starlette.responses import StreamingResponse

from .asset_store import compute_file_sha256

logger = logging.getLogger(__name__)

_RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK_SIZE = 256 * 1024  # 256 KB
_SHA256_CACHE: dict[tuple[str, float, int], str] = {}
_MAX_SHA_CACHE = 1000


def _get_file_sha256(file_path: Path) -> str:
    try:
        st = file_path.stat()
        key = (str(file_path.resolve()), st.st_mtime, st.st_size)
        if key in _SHA256_CACHE:
            return _SHA256_CACHE[key]
        sha = compute_file_sha256(file_path)
        if len(_SHA256_CACHE) >= _MAX_SHA_CACHE:
            _SHA256_CACHE.pop(next(iter(_SHA256_CACHE)))
        _SHA256_CACHE[key] = sha
        return sha
    except OSError:
        # 缓存命中失败（stat 异常），落到原始计算路径；非 OSError 类异常继续传播。
        logger.warning("sha256 cache lookup failed; recomputing", extra={"path": str(file_path)}, exc_info=True)
        return compute_file_sha256(file_path)


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int] | None:
    """解析 Range 头（bytes=0-499 / 500- / -500），返回闭区间 (start, end)，非法或不可满足时返回 None。"""
    match = _RANGE_PATTERN.match(range_header.strip())
    if not match:
        return None

    raw_start, raw_end = match.groups()

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

    return None


async def serve_ranged_file(
    request: Request,
    file_path: Path,
    media_type: str,
    *,
    content_sha256: str | None = None,
) -> Response:
    """以流式方式下发文件，支持 Range(206/416)、ETag 与不可变缓存头，避免大模型文件整体进内存。"""
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    file_size = file_path.stat().st_size
    sha256 = content_sha256 or _get_file_sha256(file_path)
    etag = f'"{sha256}"'

    base_headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Sha256": sha256,
    }

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip() in (etag, sha256, "*"):
        return Response(status_code=304, headers=base_headers)

    range_header = request.headers.get("range")

    if not range_header:

        async def full_file_iterator() -> AsyncIterator[bytes]:
            f = await asyncio.to_thread(open, file_path, "rb")
            try:
                while True:
                    chunk = await asyncio.to_thread(f.read, _CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(f.close)

        headers = {**base_headers, "Content-Length": str(file_size)}
        return StreamingResponse(full_file_iterator(), status_code=200, media_type=media_type, headers=headers)

    range_bounds = _parse_range_header(range_header, file_size)
    if range_bounds is None:
        return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{file_size}"})

    start, end = range_bounds
    chunk_length = end - start + 1

    async def ranged_iterator() -> AsyncIterator[bytes]:
        def _open_and_seek():
            fh = open(file_path, "rb")  # noqa: SIM115
            fh.seek(start)
            return fh

        f = await asyncio.to_thread(_open_and_seek)
        try:
            remaining = chunk_length
            while remaining > 0:
                to_read = min(_CHUNK_SIZE, remaining)
                chunk = await asyncio.to_thread(f.read, to_read)
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(f.close)

    ranged_headers = {
        **base_headers,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(chunk_length),
    }

    return StreamingResponse(ranged_iterator(), status_code=206, media_type=media_type, headers=ranged_headers)
