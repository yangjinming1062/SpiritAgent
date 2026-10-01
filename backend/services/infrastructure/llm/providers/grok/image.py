import asyncio
from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from ..base import ImageAsset, ImageGenProvider, ImageGenRequest, ImageGenResult, ProviderConfig
from ..http import download_as_b64, get_http


class GrokImageGenProvider(ImageGenProvider):
    """通过 xAI 的两个图像端点生图：/images/generations（纯文，返回 URL 列表，认 n 与 aspect_ratio）与 /images/edits（文+参考图，image 字段支持 URL 或 data URI）；base_url 含 /v1，原生 httpx 调用仅用相对路径以避免双前缀；xAI 默认返回 URL，统一匿名下载再 base64；xAI 协议按 aspect_ratio 驱动，size 故意忽略。2.0 起按分辨率×质量计价：resolution 固定 2k；quality 固定官方默认 medium。

    透明背景：grok-imagine-image-2.0 即使提示词直接要求透明背景，返回的仍是不带 Alpha
    通道的 RGB PNG，xAI Images API 亦无背景参数可映射；supports_transparent_background
    保持 False，透明交付走色幕兼容路径。"""

    provider_name = "grok"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.x.ai/v1"
    DEFAULT_MODEL: ClassVar[str] = "grok-imagine-image-2.0"
    # xAI /images/edits 原生消费 reference_image，工具层可直接透传，无需回退到视觉模型描述。
    supports_reference_image: ClassVar[bool] = True
    # /images/edits 是真图像编辑端点：增量重绘、保留未提及区域，官方支持多轮编辑（上一轮输出作下一轮输入）。
    supports_image_edit: ClassVar[bool] = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        payload: dict = {
            "model": self.config.model,
            "prompt": req.prompt,
            "n": req.n,
            "resolution": "2k",
            "quality": "medium",
        }
        if req.reference_image:
            payload["image"] = {"url": req.reference_image, "type": "image_url"}
        if req.aspect_ratio:
            payload["aspect_ratio"] = req.aspect_ratio

        resp = await self._client.post("/images/edits" if req.reference_image else "/images/generations", json=payload)
        body = raise_for_provider_response(resp, family=self.provider_name)

        urls = [item.get("url") for item in body.get("data") or [] if item.get("url")]
        if not urls:
            raise RuntimeError(f"grok image_gen returned no images: {body}")

        if req.response_format == "url":
            return ImageGenResult(images=[ImageAsset(url=url) for url in urls])
        b64s = await asyncio.gather(*(download_as_b64(u) for u in urls))
        return ImageGenResult(
            images=[ImageAsset(b64=b, mime="image/png") for b in b64s],
        )
