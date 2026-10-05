import asyncio
from dataclasses import replace
from typing import ClassVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..base import ChatProvider, ProviderConfig
from ..http import get_http


class LocalChatProvider(ChatProvider):
    """自托管 Responses 服务；配置与部署约束见 Backend README。"""

    provider_name = "local"
    # 默认 LM Studio；模型须显式填写已部署的模型 ID。
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:1234/v1"
    # 保守预算；服务端加载窗口须至少覆盖此值。
    CONTEXT_TOKENS: ClassVar[int] = 49_152
    requires_api_key: ClassVar[bool] = False
    # 不同本地模型的格式与推理参数支持不一致，不统一声明。
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: ProviderConfig) -> None:
        # LM Studio 默认不校验 Authorization；空密钥用占位符以满足 OpenAI SDK 构造要求。
        if not config.api_key:
            config = replace(config, api_key="local")
        super().__init__(config)
        self._llama_cpp: bool | None = None

    async def companion_reply_options(self, schema: dict, *, allow_tools: bool, repair: bool = False) -> dict:
        if repair:
            return await super().companion_reply_options(schema, allow_tools=allow_tools, repair=True)
        # llama.cpp 的自定义输出语法不能与工具语法混用；工具阶段仍由提示词约束，恢复阶段不执行工具。
        if self._llama_cpp is None:
            url = urlsplit(self.config.base_url)
            path = url.path.rstrip("/").removesuffix("/v1") + "/props"
            props_url = urlunsplit(url._replace(path=path))
            try:
                response = await asyncio.wait_for(get_http(self.config.base_url, self.config.api_key).get(props_url), 2)
                response.raise_for_status()
                props = response.json()
                self._llama_cpp = (
                    isinstance(props, dict)
                    and isinstance(props.get("build_info"), str)
                    and all(
                        isinstance(props.get(key), dict)
                        for key in ("default_generation_settings", "chat_template_caps")
                    )
                )
            except (TimeoutError, httpx.HTTPError, ValueError):
                self._llama_cpp = False
        if not self._llama_cpp or allow_tools:
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
