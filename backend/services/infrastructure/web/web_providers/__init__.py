from .base import WebDocument, WebSearchData, WebSearchItem, WebSearchProvider, WebSearchResult
from .brave_free.provider import aclose_brave
from .tavily.provider import aclose_tavily

__all__ = ["WebDocument", "WebSearchData", "WebSearchItem", "WebSearchProvider", "WebSearchResult", "aclose"]


async def aclose() -> None:
    """在 lifespan 关闭时关闭长生命周期的 ``httpx.AsyncClient`` 单例——只有 ``brave_free`` 与 ``tavily`` 持有持久客户端，``ddgs`` 不持有。"""
    await aclose_brave()
    await aclose_tavily()
