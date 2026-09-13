import asyncio
from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from ..base import ImageAsset, ImageGenProvider, ImageGenRequest, ImageGenResult, ProviderConfig
from ..http import download_as_b64, get_http


class ZhipuImageGenProvider(ImageGenProvider):
    """通过 Zhipu 的 POST /images/generations 生图（默认 glm-image，可选 cogview-4-250304）；返回 30 天临时 URL，统一下载并重编码为 base64 以屏蔽外部 URL。"""

    provider_name = "zhipu"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"image_gen": "glm-image"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"image_gen": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        payload: dict = {"model": self.config.model, "prompt": req.prompt}
        if req.size:
            payload["size"] = req.size
        if req.quality:
            payload["quality"] = req.quality

        resp = await self._client.post("/images/generations", json=payload)
        body = raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)

        # 并行下载 CDN 图，不带 Bearer——供应商返回的是短期临时 URL，统一转 base64 屏蔽外部引用。
        urls = [u for item in body.get("data") or [] if (u := item.get("url"))]
        b64s = await asyncio.gather(*(download_as_b64(u) for u in urls))
        assets = [ImageAsset(b64=b, mime="image/png") for b in b64s]

        if not assets:
            raise RuntimeError(f"Zhipu image_gen returned no images: {body}")

        return ImageGenResult(images=assets, model=self.config.model, raw=body)
