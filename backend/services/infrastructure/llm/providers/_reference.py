import base64
from urllib.parse import urlparse

from components import download_capped, is_safe_outbound


def _parse_data_uri(reference: str) -> tuple[bytes, str] | None:
    if not reference.startswith("data:"):
        return None
    meta, _, payload = reference.partition(",")
    mime = meta[5:].split(";", 1)[0] or "image/jpeg"
    try:
        return base64.b64decode(payload), mime
    except ValueError as exc:
        raise ValueError("invalid base64 payload in reference_image data URI") from exc


async def resolve_reference_bytes(reference_image: str) -> tuple[bytes, str]:
    """返回参考图的 (bytes, content_type)；接受 data URI 或 http(s) URL。"""
    from_uri = _parse_data_uri(reference_image)
    if from_uri is not None:
        return from_uri

    parsed = urlparse(reference_image)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"reference_image must be a data URI or http(s) URL: {reference_image[:64]!r}")
    hostname = parsed.hostname or ""
    safe, reason = is_safe_outbound(hostname)
    if not safe:
        raise RuntimeError(f"refusing to fetch unsafe reference host: {hostname} ({reason})")

    data = await download_capped(reference_image, max_bytes=50 * 1024 * 1024, timeout=120.0)
    ct = "image/jpeg"
    if data.startswith(b"\x89PNG"):
        ct = "image/png"
    elif data.startswith(b"RIFF") and b"WEBP" in data[:12]:
        ct = "image/webp"
    return data, ct
