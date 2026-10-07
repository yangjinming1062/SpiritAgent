import asyncio
import time
from dataclasses import replace
from typing import ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..base import ChatProvider, ProviderConfig
from ..http import get_http

# llama.cpp 构建身份按 props 地址带 TTL 缓存：负结果短期缓存堵住每回合探测，换本地服务后不会永久沿用旧结论。
_PROBE_CACHE_MAX = 64
_PROBE_TTL_SECONDS = 300.0
_PROBE_NEGATIVE_TTL_SECONDS = 60.0
_PROBE_CACHE: dict[str, tuple[bool, float]] = {}


def _cache_probe(props_url: str, is_llama_cpp: bool, now: float) -> None:
    if len(_PROBE_CACHE) >= _PROBE_CACHE_MAX:
        _PROBE_CACHE.pop(next(iter(_PROBE_CACHE)))
    ttl = _PROBE_TTL_SECONDS if is_llama_cpp else _PROBE_NEGATIVE_TTL_SECONDS
    _PROBE_CACHE[props_url] = (is_llama_cpp, now + ttl)


class LocalChatProvider(ChatProvider):
    """自托管 Responses 服务；配置与部署约束见 Backend README。"""

    provider_name = "local"
    # 默认 LM Studio；模型须显式填写已部署的模型 ID。
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:1234/v1"
    # 保守预算；服务端加载窗口须至少覆盖此值。
    CONTEXT_TOKENS: ClassVar[int] = 24_000
    requires_api_key: ClassVar[bool] = False
    # 不同本地模型的格式与推理参数支持不一致，不统一声明。
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: ProviderConfig) -> None:
        # LM Studio 默认不校验 Authorization；空密钥用占位符以满足 OpenAI SDK 构造要求。
        if not config.api_key:
            config = replace(config, api_key="local")
        super().__init__(config)

    async def companion_reply_options(self, schema: dict, *, allow_tools: bool, repair: bool = False) -> dict:
        if repair:
            return await super().companion_reply_options(schema, allow_tools=allow_tools, repair=True)
        # llama.cpp 的自定义输出语法不能与工具语法混用；工具阶段仍由提示词约束，恢复阶段不执行工具。
        if allow_tools:
            return {}
        url = urlsplit(self.config.base_url)
        path = url.path.rstrip("/").removesuffix("/v1") + "/props"
        props_url = urlunsplit(url._replace(path=path))
        now = time.monotonic()
        cached = _PROBE_CACHE.get(props_url)
        llama_cpp = cached[0] if cached is not None and cached[1] > now else None
        if llama_cpp is None:
            llama_cpp = False
            try:
                response = await asyncio.wait_for(get_http(self.config.base_url, self.config.api_key).get(props_url), 2)
                response.raise_for_status()
                props = response.json()
                llama_cpp = (
                    isinstance(props, dict)
                    and isinstance(props.get("build_info"), str)
                    and all(
                        isinstance(props.get(key), dict)
                        for key in ("default_generation_settings", "chat_template_caps")
                    )
                )
            except (TimeoutError, httpx.HTTPError, ValueError):
                pass  # 当次按非 llama.cpp 处理；负结果短期缓存，服务恢复后 TTL 过期重探
            _cache_probe(props_url, llama_cpp, now)
        if not llama_cpp:
            return {}
        # 该服务的 Responses 接口沿用 Chat Completions 的 response_format；text.format 不生效。
        return {
            "extra_body": {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "companion_reply", "strict": True, "schema": schema},
                },
            },
        }
