import httpx
from components import get_logger, safe_outbound_async_client

from ..base import WebSearchData, WebSearchItem, WebSearchProvider, WebSearchResult

logger = get_logger(__name__)

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
# 模块级客户端保持连接池预热——每次新建都要重新走 TLS 握手。
_HTTP_CLIENT = safe_outbound_async_client(timeout=15)


async def aclose_brave() -> None:
    await _HTTP_CLIENT.aclose()


class BraveFreeWebSearchProvider(WebSearchProvider):
    def __init__(self, *, api_key: str = "") -> None:
        self._api_key = api_key.strip()

    @property
    def name(self) -> str:
        return "brave-free"

    @property
    def display_name(self) -> str:
        return "Brave Search (Free)"

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def search(self, query: str, limit: int = 5) -> WebSearchResult:
        # Brave 的 `count` 上限为 20。
        count = max(1, min(int(limit), 20))

        try:
            resp = await _HTTP_CLIENT.get(
                _BRAVE_ENDPOINT,
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Brave Search HTTP error", extra={"status_code": exc.response.status_code})
            return WebSearchResult(success=False, error=f"Brave Search returned HTTP {exc.response.status_code}")
        except httpx.RequestError as exc:
            logger.warning("Brave Search request error", extra={"error_type": type(exc).__name__})
            return WebSearchResult(success=False, error="Could not reach Brave Search")

        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("Brave Search response parse error", extra={"error_type": type(exc).__name__})
            return WebSearchResult(success=False, error="Could not parse Brave Search response as JSON")

        raw_results = (data.get("web") or {}).get("results", []) or []
        truncated = raw_results[:limit]

        web_results = [
            WebSearchItem(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                description=str(r.get("description", "")),
                position=i + 1,
            )
            for i, r in enumerate(truncated)
        ]

        # 查询词来自对话内容，不写入日志
        logger.debug(
            "Brave Search complete",
            extra={"result_count": len(web_results), "raw_count": len(raw_results), "limit": limit},
        )

        return WebSearchResult(success=True, data=WebSearchData(web=web_results))
