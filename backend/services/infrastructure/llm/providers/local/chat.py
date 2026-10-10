import asyncio
import time
from dataclasses import replace
from typing import ClassVar, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..base import ChatProvider, ProviderConfig
from ..http import get_http

# 本地服务类型按 props 地址带 TTL 缓存，未识别结果短期缓存，服务变化后重探。
_PROBE_CACHE_MAX = 64
_PROBE_TTL_SECONDS = 300.0
_PROBE_NEGATIVE_TTL_SECONDS = 60.0
type _LocalApi = Literal["llama_cpp", "strata", "unknown"]
_PROBE_CACHE: dict[str, tuple[_LocalApi, float]] = {}


def _cache_probe(props_url: str, api: _LocalApi, now: float) -> None:
    if len(_PROBE_CACHE) >= _PROBE_CACHE_MAX:
        _PROBE_CACHE.pop(next(iter(_PROBE_CACHE)))
    ttl = _PROBE_TTL_SECONDS if api != "unknown" else _PROBE_NEGATIVE_TTL_SECONDS
    _PROBE_CACHE[props_url] = (api, now + ttl)


class LocalChatProvider(ChatProvider):
    """自托管 Responses 服务；配置与部署约束见 Backend README。"""

    provider_name = "local"
    # 默认 LM Studio；模型须显式填写已部署的模型 ID。
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:1234/v1"
    DEFAULT_CONTEXT_TOKENS: ClassVar[int] = 200_000
    requires_api_key: ClassVar[bool] = False
    # 已识别的 llama.cpp / Strata 下发 reasoning.effort（none 关闭思考），其他本地服务不传。
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high"})
    # 本地开源模型的推荐温度多在 0–1：归一化温度按 1:1 下发，按默认 0–2 刻度换算会让常用的 0.7 变成 1.4，长推理与工具调用明显失真。
    TEMPERATURE_MAX: ClassVar[float] = 1.0

    def __init__(self, config: ProviderConfig) -> None:
        # LM Studio 默认不校验 Authorization；空密钥用占位符以满足 OpenAI SDK 构造要求。
        if not config.api_key:
            config = replace(config, api_key="local")
        super().__init__(config)

    async def _local_api(self) -> _LocalApi:
        url = urlsplit(self.config.base_url)
        path = url.path.rstrip("/").removesuffix("/v1") + "/props"
        props_url = urlunsplit(url._replace(path=path))
        now = time.monotonic()
        cached = _PROBE_CACHE.get(props_url)
        if cached is not None and cached[1] > now:
            return cached[0]
        api: _LocalApi = "unknown"
        try:
            response = await asyncio.wait_for(get_http(self.config.base_url, self.config.api_key).get(props_url), 2)
            response.raise_for_status()
            props = response.json()
            if (
                isinstance(props, dict)
                and isinstance(props.get("build_info"), str)
                and all(
                    isinstance(props.get(key), dict) for key in ("default_generation_settings", "chat_template_caps")
                )
            ):
                api = "strata" if props["build_info"].startswith("Strata ") else "llama_cpp"
        except (TimeoutError, httpx.HTTPError, ValueError):
            pass  # 当次按未识别服务处理，TTL 过期后重探。
        _cache_probe(props_url, api, now)
        return api

    async def reasoning_param(self, requested: str | None) -> dict | None:
        return await super().reasoning_param(requested) if await self._local_api() != "unknown" else None

    async def structured_response_options(
        self,
        schema: dict,
        *,
        name: str,
        allow_tools: bool,
        repair: bool = False,
    ) -> dict:
        if repair:
            return await super().structured_response_options(schema, name=name, allow_tools=allow_tools, repair=True)
        # llama.cpp 的自定义输出语法不能与工具语法混用；工具阶段仍由提示词约束，恢复阶段不执行工具。
        if allow_tools:
            return {}
        api = await self._local_api()
        if api == "unknown":
            return {}
        if api == "strata":
            return await super().structured_response_options(schema, name=name, allow_tools=False, repair=True)
        # 该服务的 Responses 接口沿用 Chat Completions 的 response_format；text.format 不生效。
        return {
            "extra_body": {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": name, "strict": True, "schema": schema},
                },
            },
        }
