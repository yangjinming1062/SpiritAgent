from typing import Any

import httpx
from components import get_logger

from .. import WebSearchProvider

logger = get_logger(__name__)

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
# 模块级客户端保持连接池预热——每次新建都要重新走 TLS 握手。
_HTTP_CLIENT = httpx.AsyncClient(timeout=15)


async def aclose_brave() -> None:
    await _HTTP_CLIENT.aclose()


class BraveFreeWebSearchProvider(WebSearchProvider):
    def __init__(self, *, api_key: str = "") -> None:
        self._api_key = api_key.strip()

    @property
    def name(self) -> str:
        # 保留连字符形式以兼容既有配置键。
        return "brave-free"

    @property
    def display_name(self) -> str:
        return "Brave Search (Free)"

    def is_available(self) -> bool:
        return bool(self._api_key)

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    async def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        api_key = self._api_key
        if not api_key:
            return {"success": False, "error": "Brave Search API key is not configured"}

        # Brave 的 `count` 上限为 20。
        count = max(1, min(int(limit), 20))

        try:
            resp = await _HTTP_CLIENT.get(
                _BRAVE_ENDPOINT,
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Brave Search HTTP error", extra={"error": str(exc)})
            return {"success": False, "error": f"Brave Search returned HTTP {exc.response.status_code}"}
        except httpx.RequestError as exc:
            logger.warning("Brave Search request error", extra={"error": str(exc)})
            return {"success": False, "error": f"Could not reach Brave Search: {exc}"}

        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("Brave Search response parse error", extra={"error": str(exc)})
            return {"success": False, "error": "Could not parse Brave Search response as JSON"}

        raw_results = (data.get("web") or {}).get("results", []) or []
        truncated = raw_results[:limit]

        web_results = [
            {
                "title": str(r.get("title", "")),
                "url": str(r.get("url", "")),
                "description": str(r.get("description", "")),
                "position": i + 1,
            }
            for i, r in enumerate(truncated)
        ]

        logger.info(
            "Brave Search '%s': %d results (from %d raw, limit %d)",
            query,
            len(web_results),
            len(raw_results),
            limit,
        )

        return {"success": True, "data": {"web": web_results}}
