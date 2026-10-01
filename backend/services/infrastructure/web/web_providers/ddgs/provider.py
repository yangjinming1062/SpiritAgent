import asyncio
from typing import Any

from components import get_logger
from ddgs import DDGS

from .. import WebSearchProvider

logger = get_logger(__name__)


class DDGSWebSearchProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "ddgs"

    @property
    def display_name(self) -> str:
        return "DuckDuckGo (ddgs)"

    def is_available(self) -> bool:
        # ddgs 是声明依赖且在模块顶层导入，免密钥恒可用。
        return True

    def _sync_search(self, query: str, safe_limit: int) -> dict[str, Any]:
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

        # 查询词来自对话内容，不写入日志
        logger.debug("DDGS search complete", extra={"result_count": len(web_results), "limit": safe_limit})
        return {"success": True, "data": {"web": web_results}}

    async def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        # ``ddgs`` 仅同步——把阻塞 HTTP 调用投递到工作线程，避免阻塞 asyncio 事件循环。
        return await asyncio.to_thread(self._sync_search, query, max(1, int(limit)))
