import asyncio
from typing import ClassVar

import httpx

from .._provider_errors import raise_for_provider_response
from .._size_aspect import SIZE_TO_ASPECT
from ..base import ImageAsset, ImageGenProvider, ImageGenRequest, ImageGenResult, ProviderConfig
from ..http import download_as_b64, get_http


class GrokImageGenProvider(ImageGenProvider):
    """通过 xAI 的两个图像端点生图：/images/generations（纯文，返回 URL 列表，认 n 与 aspect_ratio）与 /images/edits（文+参考图，image 字段支持 URL 或 data URI）；base_url 含 /v1，原生 httpx 调用仅用相对路径以避免双前缀；xAI 默认返回 URL，统一匿名下载再 base64；xAI 协议按 aspect_ratio 驱动，size 故意忽略。"""

    provider_name = "grok"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"image_gen": "grok-imagine-image-quality"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"image_gen": 8_000}
    # xAI /images/edits 原生消费 reference_image，工具层可直接透传，无需回退到视觉模型描述。
    supports_reference_image: ClassVar[bool] = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        if req.reference_image:
            return await self._generate_with_reference(req)
        return await self._generate_text_only(req)

    async def _generate_text_only(self, req: ImageGenRequest) -> ImageGenResult:
        payload: dict = {"model": self.config.model, "prompt": req.prompt, "n": req.n}
        aspect = req.aspect_ratio or (req.size and SIZE_TO_ASPECT.get(req.size))
        if aspect:
            payload["aspect_ratio"] = aspect

        resp = await self._client.post("/images/generations", json=payload)
        body = raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)

        urls = [item.get("url") for item in body.get("data") or [] if item.get("url")]
        if not urls:
            raise RuntimeError(f"grok image_gen returned no images: {body}")

        # 并行下载 CDN 图，使用匿名客户端防止 Bearer 透出到 CDN（与 zhipu/image.py 保持一致）。
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as cdn:
            b64s = await asyncio.gather(*(download_as_b64(cdn, u) for u in urls))
        assets = [ImageAsset(b64=b, mime="image/png") for b in b64s]

        return ImageGenResult(images=assets, model=self.config.model, raw=body)

    async def _generate_with_reference(self, req: ImageGenRequest) -> ImageGenResult:
        payload: dict = {
            "model": self.config.model,
            "prompt": req.prompt,
            "n": req.n,
            "image": {"url": req.reference_image, "type": "image_url"},
        }
        aspect = req.aspect_ratio or (req.size and SIZE_TO_ASPECT.get(req.size))
        if aspect:
            payload["aspect_ratio"] = aspect

        resp = await self._client.post("/images/edits", json=payload)
        body = raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)

        urls = [item.get("url") for item in body.get("data") or [] if item.get("url")]
        if not urls:
            raise RuntimeError(f"grok image_edit returned no images: {body}")

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as cdn:
            b64s = await asyncio.gather(*(download_as_b64(cdn, u) for u in urls))
        assets = [ImageAsset(b64=b, mime="image/png") for b in b64s]

        return ImageGenResult(images=assets, model=self.config.model, raw=body)
