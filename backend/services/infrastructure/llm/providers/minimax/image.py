import json
from typing import ClassVar

from components import get_logger

from ..base import ImageAsset, ImageGenProvider, ImageGenRequest, ImageGenResult, ProviderConfig, ProviderError
from ..http import get_http
from ._errors import raise_for_minimax_response

logger = get_logger(__name__)


class MiniMaxImageGenProvider(ImageGenProvider):
    """通过 MiniMax 的 /v1/image_generation 端点生图；形态与 OpenAI Images API 不同（用 aspect_ratio+response_format 而非 size+quality），默认 base64，response_format="url" 时返回 MiniMax CDN 临时 URL。"""

    provider_name = "minimax"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.minimaxi.com"
    DEFAULT_MODEL: ClassVar[str] = "image-01"
    max_prompt_chars: ClassVar[int | None] = 1499  # 实测 ≥1500 字符的提示词被拒
    # 原生 i2i 经 subject_reference；image_file 接受公网 URL 或 data:image/*;base64,... URI。
    supports_reference_image: ClassVar[bool] = True
    # subject_reference[] 仅接受单项；MiniMax 会拒绝两项（"image_reference must be one"），数组语义为多角色场景。
    supports_multiple_reference_images: ClassVar[bool] = False

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        aspect = req.aspect_ratio or "1:1"

        payload: dict = {
            "model": self.config.model,
            "prompt": req.prompt,
            "aspect_ratio": aspect,
            "response_format": "base64" if req.response_format == "b64" else "url",
            "n": req.n,
        }
        if req.exact_size and req.size:
            width, height = (int(value) for value in req.size.lower().split("x", 1))
            payload.pop("aspect_ratio")
            payload.update(width=width, height=height)
        if req.reference_image:
            payload["subject_reference"] = [{"type": "character", "image_file": req.reference_image}]

        resp = await self._client.post("/v1/image_generation", json=payload)
        body = raise_for_minimax_response(resp)

        data = body.get("data") or {}
        assets: list[ImageAsset] = []
        if payload["response_format"] == "base64":
            for b64 in data.get("image_base64", []) or []:
                assets.append(ImageAsset(b64=b64, mime="image/jpeg"))
        else:
            for url in data.get("image_urls", []) or []:
                assets.append(ImageAsset(url=url, mime="image/jpeg"))

        if not assets:
            # 200 + base_resp 0 但 image 列表为空——可能是静默审核、subject_reference 被拒或 API 字段名变更；"returned no images" 让错误分类切换下一家。
            raw_snippet = json.dumps(body, ensure_ascii=False)[:1000]
            logger.warning(
                "minimax image_gen returned no images",
                extra={
                    "model": self.config.model,
                    "aspect_ratio": aspect,
                    "response_format": payload["response_format"],
                    "has_reference_image": bool(req.reference_image),
                    "prompt_len": len(req.prompt),
                },
            )
            raise ProviderError(f"minimax image_gen returned no images: {raw_snippet}", body=body)

        return ImageGenResult(images=assets)
