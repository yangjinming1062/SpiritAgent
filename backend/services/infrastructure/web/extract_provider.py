from collections.abc import Callable

from components import SETTINGS, get_logger

from .web_providers import WebSearchProvider
from .web_providers.brave_free import BraveFreeWebSearchProvider
from .web_providers.ddgs import DDGSWebSearchProvider
from .web_providers.tavily import TavilyWebSearchProvider

logger = get_logger(__name__)

# 每个 dispatcher 种类的默认后端，也是配置了未知后端名时的回退目标。
_DEFAULT_BY_KIND: dict[str, str] = {"search": "ddgs", "extract": "tavily"}

# 凭据在调用时从 SETTINGS 读取，管理端热更新后下一次调用即生效。
_PROVIDER_FACTORIES: dict[str, Callable[[], WebSearchProvider]] = {
    "ddgs": DDGSWebSearchProvider,
    "brave-free": lambda: BraveFreeWebSearchProvider(api_key=SETTINGS.brave_search_api_key),
    "tavily": lambda: TavilyWebSearchProvider(api_key=SETTINGS.tavily_api_key, base_url=SETTINGS.tavily_base_url),
}


def _get_provider(name: str, *, kind: str) -> WebSearchProvider:
    """构造配置项中的供应商；未知名称回退到该 kind 的默认后端并记录错误，避免误配置静默失败。"""
    factory = _PROVIDER_FACTORIES.get(name)
    if factory is None:
        fallback = _DEFAULT_BY_KIND[kind]
        logger.error(
            "Unknown web provider, falling back",
            extra={"kind": kind, "provider_name": name, "fallback": fallback},
        )
        factory = _PROVIDER_FACTORIES[fallback]
    return factory()


def resolve_search_provider() -> WebSearchProvider:
    """解析配置中的搜索后端；不可用时回退到无需密钥的默认。仅搜索路径有此回退，extract 没有对应免密钥后端。"""
    provider = _get_provider(SETTINGS.web_search_backend or _DEFAULT_BY_KIND["search"], kind="search")
    if not provider.is_available() and provider.name != _DEFAULT_BY_KIND["search"]:
        logger.info(
            "Web search provider '%s' not configured; falling back to %s",
            provider.name,
            _DEFAULT_BY_KIND["search"],
        )
        provider = _get_provider(_DEFAULT_BY_KIND["search"], kind="search")
    return provider


def resolve_extract_provider() -> WebSearchProvider:
    # 与 web_extract 的 schema 可用性判定共用，统一走 web_extract_backend → web_search_backend → 默认 的链。
    selected = SETTINGS.web_extract_backend or SETTINGS.web_search_backend or _DEFAULT_BY_KIND["extract"]
    return _get_provider(selected, kind="extract")
