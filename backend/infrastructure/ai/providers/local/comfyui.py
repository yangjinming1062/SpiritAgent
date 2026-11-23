"""图片与视频工作流共用的 ComfyUI 节点能力读取。"""

import asyncio

import httpx

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
