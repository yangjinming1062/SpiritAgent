"""Web 基础设施：搜索/抽取供应商选择与供应商实现。"""

from .extract_provider import (
    resolve_extract_provider,
    resolve_search_provider,
)
from .web_providers import WebDocument, aclose

__all__ = ["WebDocument", "aclose", "resolve_extract_provider", "resolve_search_provider"]
