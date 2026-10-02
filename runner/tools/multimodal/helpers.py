import asyncio
import base64
import io
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image
from utils import async_is_safe_url, check_website_access, create_safe_async_client

logger = logging.getLogger(__name__)

_VISION_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_BASE64_BYTES = 20 * 1024 * 1024
# 一次缩到远低于上限。
RESIZE_TARGET_BYTES = 5 * 1024 * 1024
_MIN_RESIZE_SIDE = 64
_VISION_DOWNLOAD_TIMEOUT_S = 30.0
_MODEL_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


def capped_image_data_url(image_path: Path, mime_type: str | None = None) -> str:
    """文件 → data URL；模型不接受的格式（BMP）或 base64 超过 MAX_BASE64_BYTES 时重新编码并缩放。"""
    mime = mime_type or _guess_mime_from_extension(image_path)
    if mime in _MODEL_IMAGE_MIMES and image_path.stat().st_size <= MAX_BASE64_BYTES * 3 // 4:
        data_url = _image_to_base64_data_url(image_path, mime_type=mime)
        if len(data_url) <= MAX_BASE64_BYTES:
            return data_url
    return resize_image_for_vision(image_path, mime_type=mime)


async def _validate_image_url_async(url: str) -> bool:
    return bool(
        url and isinstance(url, str) and url.startswith(("http://", "https://")) and urlparse(url).netloc,
    ) and await async_is_safe_url(url)


def _detect_image_mime_type(image_path: Path) -> str | None:
    with image_path.open("rb") as f:
        h = f.read(64)
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if h.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if h.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if h.startswith(b"BM"):
        return "image/bmp"
    if len(h) >= 12 and h.startswith(b"RIFF") and h[8:12] == b"WEBP":
        return "image/webp"
    return None


def _is_retryable_download_error(error: Exception) -> bool:
    if isinstance(error, PermissionError | ValueError):
        return False
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status == 429 or status >= 500
    # 传输层错误视为瞬时。
    return isinstance(error, httpx.TransportError | ConnectionError | OSError)


async def _download_image(image_url: str, destination: Path) -> Path:
    """下载图片到 destination，含大小上限与可重试错误处理；每跳重定向的安全校验由 SafeAsyncHTTPTransport 承担。"""

    def _write_destination(body: bytearray) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            if blocked := check_website_access(image_url):
                raise PermissionError(blocked.message)
            async with (
                create_safe_async_client(timeout=_VISION_DOWNLOAD_TIMEOUT_S, follow_redirects=True) as client,
                client.stream(
                    "GET",
                    image_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "image/*,*/*;q=0.8",
                    },
                ) as res,
            ):
                res.raise_for_status()
                if (content_length := res.headers.get("content-length")) and int(
                    content_length,
                ) > _VISION_MAX_DOWNLOAD_BYTES:
                    raise ValueError("Image too large")
                body = bytearray()
                async for chunk in res.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _VISION_MAX_DOWNLOAD_BYTES:
                        raise ValueError("Image too large")
                if blocked := check_website_access(str(res.url)):
                    raise PermissionError(blocked.message)
                await asyncio.to_thread(_write_destination, body)
            return destination
        except Exception as e:
            last_err = e
            if not _is_retryable_download_error(e) or attempt >= 2:
                logger.error(
                    "Image download failed after %s attempt(s): %s",
                    attempt + 1,
                    str(e)[:100],
                    exc_info=True,
                )
                raise
            wait = 2 ** (attempt + 1)
            logger.warning(
                "Image download failed (attempt %s/3): %s. Retrying in %ss...",
                attempt + 1,
                str(e)[:50],
                wait,
            )
            await asyncio.sleep(wait)
    raise last_err or RuntimeError("No attempts made")


def _guess_mime_from_extension(image_path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }.get(image_path.suffix.lower(), "image/jpeg")


def _image_to_base64_data_url(image_path: Path, mime_type: str | None = None) -> str:
    mime = mime_type or _guess_mime_from_extension(image_path)
    return f"data:{mime};base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"


def resize_image_for_vision(image_path: Path, mime_type: str | None = None) -> str:
    """把图片逐级缩小到 base64 不超过 RESIZE_TARGET_BYTES；PNG 保持 PNG，其余转 JPEG。"""
    try:
        img = Image.open(image_path)
    except Exception as exc:
        raise ValueError(f"image exceeds the size limit and cannot be resized: {exc}") from exc

    try:
        pil_format = "PNG" if (mime_type or _guess_mime_from_extension(image_path)) == "image/png" else "JPEG"
        if pil_format == "JPEG" and img.mode not in {"RGB", "L"}:
            converted = img.convert("RGB")
            img.close()
            img = converted
        quality_steps: tuple[int | None, ...] = (85, 70, 50) if pil_format == "JPEG" else (None,)

        # 两边减半直到进目标，短边有下限；只保留最小编码结果。
        best: str | None = None
        while True:
            for q in quality_steps:
                with io.BytesIO() as buf:
                    img.save(buf, format=pil_format, **({"quality": q} if q is not None else {}))
                    candidate = (
                        f"data:image/{pil_format.lower()};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
                    )
                if len(candidate) <= RESIZE_TARGET_BYTES:
                    return candidate
                if best is None or len(candidate) < len(best):
                    best = candidate
            new_size = (
                max(img.width // 2, min(img.width, _MIN_RESIZE_SIDE)),
                max(img.height // 2, min(img.height, _MIN_RESIZE_SIDE)),
            )
            if new_size == img.size:
                break
            resized = img.resize(new_size, Image.Resampling.LANCZOS)
            img.close()
            img = resized
        # 下限内最小结果可交付，否则失败。
        if best is not None and len(best) <= MAX_BASE64_BYTES:
            return best
        raise ValueError("image is still larger than the size limit after resizing")
    finally:
        img.close()
