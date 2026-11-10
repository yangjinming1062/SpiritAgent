import json
import math
from typing import ClassVar

import httpx

from infrastructure.ai.providers._provider_errors import response_json, response_retry_after
from infrastructure.ai.providers.base import (
    ImageAsset,
    ImageGenProvider,
    ImageGenRequest,
    ImageGenResult,
    ProviderConfig,
    ProviderError,
)
from infrastructure.ai.providers.http import get_http
from infrastructure.ai.providers.minimax._errors import raise_for_minimax_response
from infrastructure.logger import get_logger

logger = get_logger(__name__)

# 文档规定的输出面积、宽高比与单次张数上限，与 _image_canvas.select_image_canvas 的 minimax 分支保持一致。
_MIN_AREA = 655_360
_MAX_AREA = 20_000_000
_MAX_RATIO = 3.0
_MAX_IMAGES = 10
# 面积档的目标像素数，用于判断 b64_json 的总输出面积上限。
_AREA_PIXELS: dict[str, int] = {"1K": 1024**2, "2K": 2048**2, "4K": 4096**2}
_MIME_BY_OUTPUT_FORMAT: dict[str, str] = {
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}
_CONTENT_POLICY_CODES = frozenset({"1026", "1027"})


def _parse_pixel_size(value: str | None) -> tuple[int, int] | None:
    """取「宽x高」像素；比例标签（16:9）与 auto 不属于指定宽高。"""
    if not value:
        return None
    width_text, separator, height_text = value.strip().lower().partition("x")
    if not separator or not width_text.isdigit() or not height_text.isdigit():
        return None
    width, height = int(width_text), int(height_text)
    return (width, height) if width > 0 and height > 0 else None


def _normalized_aspect(value: str | None) -> str | None:
    """规范化为请求可接受的 X:Y；比例标签或像素串都能还原，越界返回 None。"""
    if not value:
        return None
    width = height = 0
    width_text, separator, height_text = value.strip().lower().partition(":")
    if separator and width_text.isdigit() and height_text.isdigit():
        width, height = int(width_text), int(height_text)
    elif pixel := _parse_pixel_size(value):
        divisor = math.gcd(*pixel)
        width, height = (dimension // divisor for dimension in pixel)
        while max(width, height) > 300:
            width, height = max(1, round(width / 2)), max(1, round(height / 2))
    if not (1 <= width <= 300 and 1 <= height <= 300):
        return None
    return f"{width}:{height}" if 1 / _MAX_RATIO <= width / height <= _MAX_RATIO else None


def _pixel_size_for_request(req: ImageGenRequest) -> str | None:
    """宽高都在文档范围内时直接下发像素尺寸；否则退回比例加面积档表达。"""
    parsed = _parse_pixel_size(req.size)
    if parsed is None:
        return None
    width, height = parsed
    area, ratio = width * height, width / height
    if _MIN_AREA <= area <= _MAX_AREA and 1 / _MAX_RATIO <= ratio <= _MAX_RATIO:
        return f"{width}x{height}"
    return None


def _output_area(payload: dict) -> int:
    if size := payload.get("size"):
        width, height = _parse_pixel_size(size) or (0, 0)
        # 尺寸异常时按上限处理，宁可退回 URL 也不误判 b64 面积可用。
        return width * height or _MAX_AREA
    return _AREA_PIXELS.get(str(payload.get("area")), _AREA_PIXELS["1K"])


def _reference_hint(req: ImageGenRequest, count: int) -> str | None:
    """多张参考图需说明编号与职责，单张身份参考直接交给模型。"""
    if count <= 1:
        return None
    labels = "、".join(f"图{index}" for index in range(1, count + 1))
    duty = "，最后一张只提供环境与光线" if req.environment_reference_image else ""
    return f"参考图按输入顺序编号为{labels}{duty}。"


def _response_failures(body: dict) -> list[dict]:
    return [
        item["error"]
        for item in body.get("data") or []
        if isinstance(item, dict) and isinstance(item.get("error"), dict)
    ]


def _policy_failure(failures: list[dict]) -> dict | None:
    """审核码优先：混合失败时仍归为内容审核，避免退化成格式错误或空结果。"""
    return next((failure for failure in failures if str(failure.get("code") or "") in _CONTENT_POLICY_CODES), None)


def _raise_error_response(resp: httpx.Response, body: dict) -> None:
    """HTTP 与 base_resp 失败统一翻译；整请求审核拒绝按单张错误码归类为内容审核。"""
    if failure := _policy_failure(_response_failures(body)):
        code = str(failure.get("code") or "")
        raise ProviderError(
            f"minimax image_gen violated safety policy ({code}): {failure.get('message', '')}",
            status_code=resp.status_code if resp.status_code >= 400 else 400,
            body={"error": {"code": code, "message": str(failure.get("message") or "")}},
            retry_after_seconds=response_retry_after(resp),
        )
    raise_for_minimax_response(resp)


def _no_images_error(body: dict, failures: list[dict]) -> ProviderError:
    policy = _policy_failure(failures)
    if policy:
        code = str(policy.get("code") or "")
        message = str(policy.get("message") or "")
        return ProviderError(
            f"minimax image_gen violated safety policy ({code}): {message}",
            status_code=422,
            body={"error": {"code": code, "message": message}},
        )
    first = failures[0] if failures else {}
    code = str(first.get("code") or "")
    message = str(first.get("message") or "")
    snippet = json.dumps(body, ensure_ascii=False)[:1000]
    return ProviderError(
        f"minimax image_gen returned no images: {snippet}",
        body={"error": {"code": code, "message": message}} if failures else body,
    )


class MiniMaxImageGenProvider(ImageGenProvider):
    """通过 MiniMax-H3-Image 的 /v1/images/generations 与 /v1/images/edits 生图：尺寸用「宽x高」或「宽高比+面积档」二选一，带参考图走编辑端点，返回 URL 或 b64_json。原生透明背景不支持，保持基类默认。"""

    provider_name = "minimax"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.minimaxi.com"
    max_prompt_chars: ClassVar[int | None] = 32000  # 文档上限
    max_images_per_request: ClassVar[int | None] = _MAX_IMAGES
    # 编辑端点接受 1~16 张参考图；身份条件化与图像编辑共用同一入口。
    supports_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True
    supports_environment_reference_image: ClassVar[bool] = True
    supports_image_edit: ClassVar[bool] = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        n = max(1, min(_MAX_IMAGES, int(req.n or 1)))
        references = [
            value
            for value in (req.reference_image, req.secondary_reference_image, req.environment_reference_image)
            if value
        ]
        prompt = req.prompt
        if hint := _reference_hint(req, len(references)):
            prompt = f"{hint}\n\n{prompt}"

        payload: dict = {"model": self.config.model, "prompt": prompt, "n": n}
        if size := _pixel_size_for_request(req):
            payload["size"] = size
        else:
            payload["aspect_ratio"] = _normalized_aspect(req.aspect_ratio) or _normalized_aspect(req.size) or "1:1"
            # 缺省面积档为 1K，避免服务端按默认 4K 生成十几倍面积。
            payload["area"] = req.resolution if req.resolution in _AREA_PIXELS else "1K"
        # b64_json 有总输出面积上限，超额时改用 URL 由媒体层下载转存。
        b64 = req.response_format == "b64" and n * _output_area(payload) <= _MAX_AREA
        payload["response_format"] = "b64_json" if b64 else "url"
        if references:
            payload["images"] = [{"image_url": value} for value in references]

        resp = await self._client.post("/v1/images/edits" if references else "/v1/images/generations", json=payload)
        body = response_json(resp)
        if resp.status_code >= 400 or body.get("base_resp"):
            _raise_error_response(resp, body)

        mime = _MIME_BY_OUTPUT_FORMAT.get(str(body.get("output_format") or "").lower(), "image/jpeg")
        assets: list[ImageAsset] = []
        failures: list[dict] = []
        for item in body.get("data") or []:
            if not isinstance(item, dict):
                continue
            if error := item.get("error"):
                failures.append(error if isinstance(error, dict) else {"message": str(error)})
                continue
            if b64:
                if value := item.get("b64_json"):
                    assets.append(ImageAsset(b64=value, mime=mime))
            elif url := item.get("url"):
                assets.append(ImageAsset(url=url, mime=mime))

        if not assets:
            logger.warning(
                "minimax image_gen returned no images",
                extra={
                    "model": self.config.model,
                    "requested": n,
                    "response_format": payload["response_format"],
                    "failed": len(failures),
                    "reference_count": len(references),
                    "prompt_len": len(req.prompt),
                },
            )
            raise _no_images_error(body, failures)
        if failures or len(assets) < n:
            logger.warning(
                "minimax image_gen partial results",
                extra={"model": self.config.model, "requested": n, "delivered": len(assets), "failed": len(failures)},
            )
        return ImageGenResult(images=assets)
