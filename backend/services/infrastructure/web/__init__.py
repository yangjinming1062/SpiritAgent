"""Web 基础设施：搜索/抽取供应商选择与供应商实现。"""

from services.infrastructure.web.extract_provider import (
    resolve_extract_provider,
    resolve_search_provider,
)
from services.infrastructure.web.web_providers import WebSearchProvider, aclose

__all__ = ["WebSearchProvider", "aclose", "resolve_extract_provider", "resolve_search_provider"]
