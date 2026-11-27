"""图片与视频工作流共用的 ComfyUI 节点能力读取和参考素材上传。"""

import asyncio
import hashlib
from collections.abc import Iterable

import httpx

from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.ai.providers.base import ProviderError
from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.logger import get_logger

logger = get_logger(__name__)


async def loader_options(client: httpx.AsyncClient, node: str, field: str) -> list[str]:
    response = await client.get(f"/object_info/{node}", timeout=10)
    response.raise_for_status()
    try:
        spec = response.json()[node]["input"]["required"][field]
    except (KeyError, TypeError):
        return []
    if not isinstance(spec, list) or not spec:
        return []
    if isinstance(spec[0], list):
        options = spec[0]
    elif len(spec) > 1 and isinstance(spec[1], dict):
        options = spec[1].get("options", [])
    else:
        return []
    return [name for name in options if isinstance(name, str)] if isinstance(options, list) else []


async def supports_nodes(client: httpx.AsyncClient, *names: str) -> bool:
    try:
        responses = await asyncio.gather(*(client.get(f"/object_info/{name}", timeout=10) for name in names))
        for name, response in zip(names, responses, strict=True):
            info = response.json() if response.is_success else None
            if not isinstance(info, dict) or not isinstance(info.get(name), dict):
                return False
        return True
    except (httpx.HTTPError, ValueError):
        logger.info("Optional ComfyUI nodes unavailable", extra={"nodes": names}, exc_info=True)
        return False


async def _upload_reference(client: httpx.AsyncClient, reference: str, *, video: bool) -> str:
    data, mime = await resolve_reference_bytes(reference)
    if len(data) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
        raise ProviderError("ComfyUI reference exceeds the configured size limit", status_code=413)
    if video:
        if data[4:8] == b"ftyp":
            mime = "video/mp4"
        extension = {"video/mp4": "mp4", "video/quicktime": "mov"}.get(mime, "webm")
    else:
        extension = "jpg" if "jpeg" in mime else "webp" if "webp" in mime else "png"
    digest = await asyncio.to_thread(hashlib.sha256, data)
    filename = f"ref_{digest.hexdigest()}.{extension}"
    response = await client.post(
        "/upload/image",
        files={"image": (filename, data, mime)},
        data={"overwrite": "true", "type": "input"},
    )
    if response.status_code >= 400:
        raise ProviderError(
            f"ComfyUI reference upload failed: {response.status_code} {response.text[:300]}",
            status_code=response.status_code,
            body={"text": response.text[:500]},
        )
    body = response.json()
    name = body.get("name") or filename
    subfolder = body.get("subfolder")
    return f"{subfolder}/{name}" if subfolder else name


async def upload_references(
    client: httpx.AsyncClient, references: Iterable[str], *, video: bool = False
) -> dict[str, str]:
    """每份输入只上传一次，调用方按各参考位置取回文件名。"""
    return {ref: await _upload_reference(client, ref, video=video) for ref in dict.fromkeys(references)}
