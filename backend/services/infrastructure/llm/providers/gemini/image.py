import base64
from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from .._reference import resolve_reference_bytes
from ..base import ImageAsset, ImageGenProvider, ImageGenRequest, ImageGenResult, ProviderConfig
from ..http import get_http
from ._parts import iter_parts


class GeminiImageGenProvider(ImageGenProvider):
    """通过 Gemini generateContent 生成 2K 图像；参考图以内联部件置于文本前。不支持原生透明背景。"""

    provider_name = "gemini"
    DEFAULT_BASE_URL: ClassVar[str] = "https://generativelanguage.googleapis.com"
    DEFAULT_MODEL: ClassVar[str] = "gemini-3-pro-image"
    supports_reference_image: ClassVar[bool] = True
    supports_environment_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True
    # inlineData + 文本触发原生图像编辑（增量重绘、保留未提及区域），多轮迭代可无状态地把上一轮输出再喂回。
    supports_image_edit: ClassVar[bool] = True
    max_images_per_request: ClassVar[int | None] = 1

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        aspect = req.aspect_ratio or "1:1"

        parts: list[dict] = []
        for reference in (req.reference_image, req.secondary_reference_image):
            if reference:
                data, mime = await resolve_reference_bytes(reference)
                parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("utf-8")}})
        parts.append({"text": req.prompt})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": aspect, "imageSize": req.resolution or "2K"},
            },
        }

        resp = await self._client.post(f"/v1beta/models/{self.config.model}:generateContent", json=payload)
        body = raise_for_provider_response(resp, family="gemini")

        assets: list[ImageAsset] = []
        for part in iter_parts(body):
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                assets.append(ImageAsset(b64=inline["data"], mime=inline.get("mimeType", "image/png")))

        if not assets:
            raise RuntimeError(f"Gemini image_gen returned no images: {body}")

        return ImageGenResult(images=assets)
