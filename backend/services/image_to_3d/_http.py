import httpx
from components import safe_outbound_async_client

DEFAULT_TIMEOUT_SECONDS: float = 60.0


async def post_json(
    base_url: str,
    headers: dict[str, str],
    path: str,
    payload: dict,
    *,
    timeout: float | httpx.Timeout = DEFAULT_TIMEOUT_SECONDS,
) -> httpx.Response:
    """POST JSON 到供应商端点；带 SSRF 守卫的 httpx 客户端。

    每次调用新建 AsyncClient（``safe_outbound_async_client`` 是工厂函数）——本函数
    不提供跨调用连接池；仅统一 SSRF 守卫与 ``base_url + path`` 拼装。
    """
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    async with safe_outbound_async_client(timeout=timeout) as client:
        return await client.post(url, headers=headers, json=payload)


async def get_json(
    base_url: str,
    headers: dict[str, str],
    path: str,
    *,
    timeout: float | httpx.Timeout = DEFAULT_TIMEOUT_SECONDS,
) -> httpx.Response:
    """GET 供应商端点。"""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    async with safe_outbound_async_client(timeout=timeout) as client:
        return await client.get(url, headers=headers)
