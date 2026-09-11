from components import SETTINGS, get_logger

from .web_providers import WebSearchProvider
from .web_providers.brave_free import BraveFreeWebSearchProvider
from .web_providers.ddgs import DDGSWebSearchProvider
from .web_providers.tavily import TavilyWebSearchProvider

logger = get_logger(__name__)

# 每个 dispatcher 种类的默认后端，是工具路径与 ``_get_provider`` 异常回退的「未配置」兜底来源。
_DEFAULT_BY_KIND: dict[str, str] = {"search": "ddgs", "extract": "tavily"}

_PROVIDERS: dict[str, type[WebSearchProvider]] = {
    "ddgs": DDGSWebSearchProvider,
    "brave-free": BraveFreeWebSearchProvider,
    "tavily": TavilyWebSearchProvider,
}

# ``provider.__init__`` kwargs → ``SETTINGS`` 字段名；空字典表示该供应商无需凭据（``cls(**{})`` 退化为 ``cls()``）。
_PROVIDER_SETTING_FIELDS: dict[str, dict[str, str]] = {
    "brave-free": {"api_key": "brave_search_api_key"},
    "tavily": {"api_key": "tavily_api_key", "base_url": "tavily_base_url"},
}


def _resolve_provider_name(name: str | None, *, kind: str) -> str:
    """将配置项中的供应商名解析到已知后端；未知名称会回退到该 kind 的默认后端，避免误配置静默失败。"""
    if name in _PROVIDERS:
        return name
    fallback = _DEFAULT_BY_KIND[kind]
    logger.error(
        "Unknown web provider, falling back",
        extra={"kind": kind, "provider_name": name, "fallback": fallback},
    )
    return fallback


def _get_provider(provider_name: str | None, *, kind: str = "search") -> WebSearchProvider:
    name = _resolve_provider_name(provider_name, kind=kind)
    try:
        cls = _PROVIDERS[name]
        kwargs = {param: getattr(SETTINGS, field) for param, field in _PROVIDER_SETTING_FIELDS.get(name, {}).items()}
        return cls(**kwargs)
    except Exception as e:
        logger.error("Error loading web provider", extra={"provider_name": name, "error": str(e)})
        return _PROVIDERS[_DEFAULT_BY_KIND[kind]]()


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
