import asyncio
from typing import ClassVar

from ..base import ImageAsset, ImageGenProvider, ImageGenRequest, ImageGenResult, ProviderConfig
from ..http import download_as_b64, get_http
from ._errors import raise_for_qwen_response

# 千问文生图/图像编辑用「宽*高」；尺寸保持请求的画幅比例。
_ASPECT_TO_SIZE: dict[str, str] = {
    "1:1": "1024*1024",
    "16:9": "1280*720",
    "9:16": "720*1280",
    "4:3": "1152*864",
    "3:4": "864*1152",
    "3:2": "1152*768",
    "2:3": "768*1152",
    "21:9": "1512*648",
}


def _resolve_size(req: ImageGenRequest) -> str | None:
    if size := _ASPECT_TO_SIZE.get(req.aspect_ratio or ""):
        return size
    # 未登记画幅时按请求像素串（1024x1792 或 宽*高）规范化。
    return req.size.replace("x", "*").replace("X", "*") if req.size else None


class QwenImageGenProvider(ImageGenProvider):
    """通过千问 MultiModalConversation 文生图/图像编辑，默认 qwen-image-3.0-pro。"""

    provider_name = "qwen"
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/api/v1"
    DEFAULT_MODEL: ClassVar[str] = "qwen-image-3.0-pro"
    supports_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True
    supports_image_edit: ClassVar[bool] = True
    max_images_per_request: ClassVar[int | None] = 6

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        content: list[dict] = []
        if req.reference_image:
            content.append({"image": req.reference_image})
        if req.secondary_reference_image:
            content.append({"image": req.secondary_reference_image})
        content.append({"text": req.prompt})

        parameters: dict = {
            "result_format": "message",
            "n": max(1, min(6, int(req.n or 1))),
            "watermark": False,
            "prompt_extend": not bool(req.reference_image),
        }
        size = _resolve_size(req)
        if size:
            parameters["size"] = size

        payload = {
            "model": self.config.model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }
        resp = await self._client.post("/services/aigc/multimodal-generation/generation", json=payload)
        body = raise_for_qwen_response(resp)

        urls: list[str] = []
        for choice in (body.get("output") or {}).get("choices") or []:
            for item in (choice.get("message") or {}).get("content") or []:
                if u := item.get("image"):
                    urls.append(u)
        if not urls:
            raise RuntimeError(f"qwen image_gen returned no images: {body}")

        if req.response_format == "url":
            return ImageGenResult(images=[ImageAsset(url=url) for url in urls], model=self.config.model, raw=body)
        b64s = await asyncio.gather(*(download_as_b64(u) for u in urls))
        return ImageGenResult(
            images=[ImageAsset(b64=b, mime="image/png") for b in b64s],
            model=self.config.model,
            raw=body,
        )
