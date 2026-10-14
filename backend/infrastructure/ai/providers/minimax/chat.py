from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, ClassVar

from openai import AsyncOpenAI

from infrastructure.ai.providers._companion_format import ChatSchemaResponsesClient
from infrastructure.ai.providers.base import ChatProvider


def _remove_numeric_enums(value: Any) -> None:
    if isinstance(value, dict):
        if value.get("type") in ("integer", "number") and isinstance(value.get("enum"), list):
            value.pop("enum")
        for child in value.values():
            _remove_numeric_enums(child)
    elif isinstance(value, list):
        for child in value:
            _remove_numeric_enums(child)


class MiniMaxChatProvider(ChatProvider):
    """通过 MiniMax /v1/responses 提供 chat；M3 支持 image_url 输入，M2.x 仅文本。"""

    provider_name = "minimax"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.minimaxi.com/v1"
    # 文本与视觉共用 M3。
    supports_image: ClassVar[bool] = True
    # M3 的 /v1/responses 接受扁平 input_video（data URL 实测 49MB 可用；容器仅 mp4/mov，webm 被拒）。
    supports_video: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "minimal", "low", "medium", "high"})
    # MiniMax /v1/responses 文档支持区间 (0, 1.0]，下限垫 0.01
    TEMPERATURE_MIN: ClassVar[float] = 0.01
    TEMPERATURE_MAX: ClassVar[float] = 1.0

    def structured_response_client(
        self,
        *,
        repair: bool,
        before_submit: Callable[[], Awaitable[None]] | None = None,
    ) -> AsyncOpenAI | ChatSchemaResponsesClient:
        if repair:
            return ChatSchemaResponsesClient(
                self.raw_client(),
                before_submit=before_submit,
                chat_extra_body={"reasoning_split": True},
            )
        return super().structured_response_client(repair=repair, before_submit=before_submit)

    async def structured_response_options(
        self,
        schema: dict,
        *,
        name: str,
        allow_tools: bool,
        repair: bool = False,
    ) -> dict:
        options = await super().structured_response_options(schema, name=name, allow_tools=allow_tools, repair=repair)
        if repair:
            # 修复端点把 enum 元素解析为字符串，拒绝整数枚举；来源授权仍由应用校验。
            options = deepcopy(options)
            _remove_numeric_enums(options["text"]["format"]["schema"])
        return options
