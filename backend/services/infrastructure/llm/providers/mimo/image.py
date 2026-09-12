from typing import ClassVar

from openai import AsyncOpenAI

from ..base import ImageAsset, ImageGenProvider, ImageGenRequest, ImageGenResult, ProviderConfig
from ..http import get_async_client


class MiMoImageGenProvider(ImageGenProvider):
    """通过 OpenAI Images API 生图（兼容任何暴露 client.images.generate() 的供应商，如 DALL·E）；线缆形态由 OpenAI SDK 处理。"""

    provider_name = "mimo"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"image_gen": "dall-e-3"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"image_gen": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        kwargs: dict = {"model": self.config.model, "prompt": req.prompt, "n": req.n}
        if req.size:
            kwargs["size"] = req.size
        if req.quality:
            kwargs["quality"] = req.quality
        response = await self._client.images.generate(**kwargs)
        assets: list[ImageAsset] = []
        for item in response.data:
            assets.append(
                ImageAsset(url=getattr(item, "url", None), b64=getattr(item, "b64_json", None), mime="image/png"),
            )
        return ImageGenResult(images=assets, model=self.config.model, raw=response)
