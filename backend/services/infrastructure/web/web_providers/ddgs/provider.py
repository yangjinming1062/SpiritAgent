import asyncio
from importlib import util
from typing import Any

from components import get_logger
from ddgs import DDGS

from .. import WebSearchProvider

logger = get_logger(__name__)


def _ddgs_importable() -> bool:
    return util.find_spec("ddgs") is not None


class DDGSWebSearchProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "ddgs"

    @property
    def display_name(self) -> str:
        return "DuckDuckGo (ddgs)"

    def is_available(self) -> bool:
        return _ddgs_importable()

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def _sync_search(self, query: str, safe_limit: int) -> dict[str, Any]:
        if not _ddgs_importable():
            return {"success": False, "error": "ddgs package is not installed — run `pip install ddgs`"}

        web_results = []
        try:
            with DDGS() as client:
                for i, hit in enumerate(client.text(query, max_results=safe_limit)):
                    url = str(hit.get("href") or hit.get("url") or "")
                    web_results.append(
                        {
                            "title": str(hit.get("title", "")),
                            "url": url,
                            "description": str(hit.get("body", "")),
                            "position": i + 1,
                        },
                    )
        except Exception as exc:
            logger.warning("DDGS search error", extra={"error": str(exc)})
            return {"success": False, "error": f"DuckDuckGo search failed: {exc}"}

        logger.info(
            "DDGS search complete",
            extra={"query": query, "result_count": len(web_results), "limit": safe_limit},
        )
        return {"success": True, "data": {"web": web_results}}

    async def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        # ``ddgs`` 仅同步——把阻塞 HTTP 调用投递到工作线程，避免阻塞 asyncio 事件循环。
        return await asyncio.to_thread(self._sync_search, query, max(1, int(limit)))
