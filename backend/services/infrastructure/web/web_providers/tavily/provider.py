from typing import Any

from components import get_logger, safe_outbound_async_client

from .. import WebSearchProvider

logger = get_logger(__name__)

TAVILY_TIMEOUT = 60
TAVILY_DEFAULT_BASE_URL = "https://api.tavily.com"
# 模块级客户端保持连接池预热——每次新建都要重新走 TLS 握手。
_HTTP_CLIENT = safe_outbound_async_client(timeout=TAVILY_TIMEOUT)


async def aclose_tavily() -> None:
    await _HTTP_CLIENT.aclose()


def _normalize_tavily_search_results(response: dict[str, Any]) -> dict[str, Any]:
    """将 Tavily ``/search`` 响应映射为 ``{success, data: {web: [...]}}`` 格式。"""
    web_results = [
        {
            "title": result.get("title", ""),
            "url": result.get("url", ""),
            "description": result.get("content", ""),
            "position": i + 1,
        }
        for i, result in enumerate(response.get("results", []))
    ]
    return {"success": True, "data": {"web": web_results}}


def _failed_document(url: str, error: str) -> dict[str, Any]:
    return {"url": url, "title": "", "content": "", "raw_content": "", "error": error, "metadata": {"sourceURL": url}}


def _normalize_tavily_documents(response: dict[str, Any], fallback_url: str = "") -> list[dict[str, Any]]:
    """将 Tavily ``/extract`` 响应映射为标准文档；失败项（``failed_results``、``failed_urls``）转为带 ``error`` 字段的条目而非抛错。"""
    documents: list[dict[str, Any]] = []
    for result in response.get("results", []):
        url = result.get("url", fallback_url)
        raw = result.get("raw_content", "") or result.get("content", "")
        documents.append(
            {
                "url": url,
                "title": result.get("title", ""),
                "content": raw,
                "raw_content": raw,
                "metadata": {"sourceURL": url, "title": result.get("title", "")},
            },
        )
    for fail in response.get("failed_results", []):
        documents.append(_failed_document(fail.get("url", fallback_url), fail.get("error", "extraction failed")))
    for fail_url in response.get("failed_urls", []):
        url_str = fail_url if isinstance(fail_url, str) else str(fail_url)
        documents.append(_failed_document(url_str, "extraction failed"))
    return documents


class TavilyWebSearchProvider(WebSearchProvider):
    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.strip() or TAVILY_DEFAULT_BASE_URL

    @property
    def name(self) -> str:
        return "tavily"

    @property
    def display_name(self) -> str:
        return "Tavily"

    def is_available(self) -> bool:
        return bool(self._api_key)

    def supports_extract(self) -> bool:
        return True

    def missing_credential_message(self) -> str:
        # Tavily 是目前唯一支持 extract 的供应商，``web_extract`` 因缺凭据失败时模型看到的就是这条文案；配置由运维在管理后台完成。
        return "Web page extraction is not configured on this server, so web_extract is unavailable."

    async def _request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """调用方先经 is_available 确认密钥存在。"""
        url = f"{self._base_url}/{endpoint}"
        logger.info("Tavily request", extra={"endpoint": endpoint, "url": url})
        response = await _HTTP_CLIENT.post(url, json={**payload, "api_key": self._api_key})
        response.raise_for_status()
        return response.json()

    async def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        try:
            logger.debug("Tavily search", extra={"limit": limit})
            raw = await self._request(
                "search",
                {"query": query, "max_results": min(limit, 20), "include_raw_content": False, "include_images": False},
            )
            return _normalize_tavily_search_results(raw)
        except Exception as exc:
            logger.warning("Tavily search error", extra={"error": str(exc)})
            return {"success": False, "error": f"Tavily search failed: {exc}"}

    async def extract(self, urls: list[str]) -> list[dict[str, Any]]:
        try:
            logger.info("Tavily extract", extra={"url_count": len(urls)})
            raw = await self._request("extract", {"urls": urls, "include_images": False})
            return _normalize_tavily_documents(raw, fallback_url=urls[0] if urls else "")
        except Exception as exc:
            logger.warning("Tavily extract error", extra={"error": str(exc)})
            return [_failed_document(u, f"Tavily extract failed: {exc}") for u in urls]
