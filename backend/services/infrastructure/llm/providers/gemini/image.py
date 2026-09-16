import base64
from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from .._reference import resolve_reference_bytes
from .._size_aspect import SIZE_TO_ASPECT
from ..base import ImageAsset, ImageGenProvider, ImageGenRequest, ImageGenResult, ProviderConfig
from ..http import get_http
from ._parts import iter_parts


class GeminiImageGenProvider(ImageGenProvider):
    """通过 Gemini 的 generateContent（responseModalities=["TEXT","IMAGE"]，Gemini 3 图像模型强制双模态）生成图像；imageSize 固定 2K（仅 3 Pro 支持 4K，取值严格大写 K）；reference_image 作为 inlineData 部件置于文本前，触发 Gemini 原生图像编辑模式（保留主体、按提示重绘）。"""

    provider_name = "gemini"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"image_gen": "gemini-3-pro-image"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"image_gen": 8_000}
    supports_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True
    # inlineData + 文本触发原生图像编辑（增量重绘、保留未提及区域），多轮迭代可无状态地把上一轮输出再喂回。
    supports_image_edit: ClassVar[bool] = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        aspect = req.aspect_ratio or (req.size and SIZE_TO_ASPECT.get(req.size)) or "1:1"

        parts: list[dict] = []
        if req.reference_image:
            data, mime = await resolve_reference_bytes(req.reference_image)
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("utf-8")}})
        if req.secondary_reference_image:
            data, mime = await resolve_reference_bytes(req.secondary_reference_image)
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("utf-8")}})
        parts.append({"text": req.prompt})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": aspect, "imageSize": "2K"},
            },
        }

        resp = await self._client.post(f"/v1beta/models/{self.config.model}:generateContent", json=payload)
        body = raise_for_provider_response(resp, family="gemini", model=self.config.model)

        assets: list[ImageAsset] = []
        for part in iter_parts(body):
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                assets.append(ImageAsset(b64=inline["data"], mime=inline.get("mimeType", "image/png")))

        if not assets:
            raise RuntimeError(f"Gemini image_gen returned no images: {body}")

        return ImageGenResult(images=assets, model=self.config.model, raw=body)
