import asyncio
from typing import ClassVar

from .._size_aspect import SIZE_TO_ASPECT
from ..base import ImageAsset, ImageGenProvider, ImageGenRequest, ImageGenResult, ProviderConfig
from ..http import download_as_b64, get_http
from ._errors import raise_for_qwen_response

# 千问文生图/图像编辑用「宽*高」；aspect 按官方推荐尺寸映射。
_ASPECT_TO_SIZE: dict[str, str] = {
    "1:1": "1024*1024",
    "16:9": "1280*720",
    "9:16": "720*1280",
    "4:3": "1152*768",
    "3:4": "768*1152",
}


def _resolve_size(req: ImageGenRequest) -> str | None:
    if req.size:
        # OpenAI 像素串（1024x1792）或已是「宽*高」时直接规范化
        if "x" in req.size.lower() or "*" in req.size:
            return req.size.replace("x", "*").replace("X", "*")
        mapped = _ASPECT_TO_SIZE.get(req.size) or _ASPECT_TO_SIZE.get(SIZE_TO_ASPECT.get(req.size, ""))
        if mapped:
            return mapped
        return req.size.replace("x", "*").replace("X", "*")
    aspect = req.aspect_ratio or SIZE_TO_ASPECT.get(req.size or "")
    return _ASPECT_TO_SIZE.get(aspect or "")


class QwenImageGenProvider(ImageGenProvider):
    """通过千问 MultiModalConversation 文生图/图像编辑，默认 qwen-image-3.0-pro。"""

    provider_name = "qwen"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"image_gen": "qwen-image-3.0-pro"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"image_gen": 8_000}
    supports_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True
    supports_image_edit: ClassVar[bool] = True

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
            "prompt_extend": True,
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
        body = raise_for_qwen_response(resp, family=self.provider_name, model=self.config.model)

        urls: list[str] = []
        for choice in (body.get("output") or {}).get("choices") or []:
            for item in (choice.get("message") or {}).get("content") or []:
                if u := item.get("image"):
                    urls.append(u)
        if not urls:
            raise RuntimeError(f"qwen image_gen returned no images: {body}")

        b64s = await asyncio.gather(*(download_as_b64(u) for u in urls))
        assets = [ImageAsset(b64=b, mime="image/png") for b in b64s]
        return ImageGenResult(images=assets, model=self.config.model, raw=body)
