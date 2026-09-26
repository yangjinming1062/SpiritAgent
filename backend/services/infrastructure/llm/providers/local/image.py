"""本地 qwen 图像生成供应商（ComfyUI HTTP API）。ComfyUI 须能从 Backend 访问。"""

import asyncio
import base64
import hashlib
import time
from io import BytesIO
from typing import Any, ClassVar

from components import REMOTE_ASSET_DOWNLOAD_MAX_BYTES, get_logger
from PIL import Image

from .._reference import resolve_reference_bytes
from .._size_aspect import SIZE_TO_ASPECT
from ..base import (
    ImageAsset,
    ImageGenProvider,
    ImageGenRequest,
    ImageGenResult,
    ProviderConfig,
    ProviderError,
)
from ..http import get_http

logger = get_logger(__name__)

# 画幅短边不低于 1024（与 SIZE_TO_ASPECT 体系一致）。
_ASPECT_TO_WH: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "16:9": (1792, 1024),
    "9:16": (1024, 1792),
    "4:3": (1360, 1024),
    "3:4": (1024, 1360),
    "3:2": (1536, 1024),
    "2:3": (1024, 1536),
    "21:9": (2048, 880),
}

_RGBA_WRAP = (
    "This is an RGBA format image with transparency. "
    "{desc}. "
    "The image has an alpha channel and a transparent background."
)

_POLL_INTERVAL_S = 2.0
# Mac MPS 单张约 10–12 分钟（25 步）；多参考编辑与 2K 更慢。任务总等待按最坏 1 小时预算，
# 单次 HTTP（提交/轮询/取图）由 llm_request_timeout_seconds 约束，不在此重复包一层。
_JOB_TIMEOUT_S = 3600.0
_STEPS = 25


def _wrap_rgba(prompt: str) -> str:
    return _RGBA_WRAP.format(desc=prompt.strip())


def _has_transparency(image_b64: str) -> bool:
    try:
        data = base64.b64decode(image_b64, validate=True)
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or ("A" not in image.getbands() and "transparency" not in image.info):
                return False
            low, high = image.convert("RGBA").getchannel("A").getextrema()
            return low < 255 and high > 0
    except Exception:
        return False


def _resolve_wh(req: ImageGenRequest) -> tuple[int, int]:
    if req.size and "x" in req.size.lower():
        try:
            w_str, h_str = req.size.lower().split("x", 1)
            w, h = int(w_str), int(h_str)
            if w > 0 and h > 0:
                return (max(64, w // 32 * 32), max(64, h // 32 * 32))
        except ValueError:
            pass
    aspect = req.aspect_ratio or SIZE_TO_ASPECT.get(req.size or "")
    return _ASPECT_TO_WH.get(aspect or "", (1024, 1024))


def _t2i_graph(
    *,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    batch_size: int,
    unet_name: str,
    clip_name: str,
    vae_name: str,
) -> dict[str, Any]:
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": clip_name, "type": "qwen_image", "device": "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "4": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["2", 0],
                "prompt": prompt,
                "negative_prompt": "",
                "resolution": min(width, height),
            },
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": batch_size},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["4", 1],
                "latent_image": ["5", 0],
                "seed": seed,
                "steps": _STEPS,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
            },
        },
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {
            "class_type": "PreviewImage",
            "inputs": {"images": ["7", 0]},
        },
    }


def _edit_graph(
    *,
    prompt: str,
    seed: int,
    image_names: list[str],
    unet_name: str,
    clip_name: str,
    vae_name: str,
    width: int,
    height: int,
    image_edit: bool = False,
) -> dict[str, Any]:
    """带参考图工作流。

    ``image_edit=True`` 时采样 latent 取编码节点输出，画布随第一张参考图；
    否则用 width×height 的 EmptyLatentImage，输出尺寸服从请求 size/aspect。
    """
    latent_image: list[Any] = ["4", 2] if image_edit else ["5", 0]
    nodes: dict[str, Any] = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": clip_name, "type": "qwen_image", "device": "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "4": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["2", 0],
                "vae": ["3", 0],
                "prompt": prompt,
                "negative_prompt": "",
                "resolution": 0,
            },
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["4", 1],
                "latent_image": latent_image,
                "seed": seed,
                "steps": _STEPS,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
            },
        },
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {
            "class_type": "PreviewImage",
            "inputs": {"images": ["7", 0]},
        },
    }
    if not image_edit:
        nodes["5"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        }
    for i, name in enumerate(image_names, start=1):
        load_id = f"1{i}"
        nodes[load_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        nodes["4"]["inputs"][f"images.image_{i}"] = [load_id, 0]
    return nodes


class LocalImageGenProvider(ImageGenProvider):
    """ComfyUI 生图；默认 base_url 指向 localhost，部署后可改为远程地址。

    透明请求会包装提示词，并在返回前核验 PNG 含可见透明像素。
    """

    provider_name = "local"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"image_gen": "qwen"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"image_gen": 8_000}
    # 本地 ComfyUI 无鉴权；api_key 可为空，仅占位。
    requires_api_key: ClassVar[bool] = False
    supports_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True
    supports_image_edit: ClassVar[bool] = True
    supports_transparent_background: ClassVar[bool] = True
    # Mac 16GB 统一内存不宜并行 batch；单次原生请求只出 1 张，多张由调用方或本层顺序重试。
    max_images_per_request: ClassVar[int | None] = 1

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        # ComfyUI 不校验 Authorization；空 auth_header 避免带 Bearer 占位。
        # 本地上传参考图 / 拉取出图可能接近 llm_request_timeout；单次请求超时仍远小于任务总预算。
        self._client = get_http(config.base_url, config.api_key or "local", auth_header={})
        self._model_files: tuple[str, str, str] | None = None

    async def _resolve_model_files(self) -> tuple[str, str, str]:
        if self._model_files is not None:
            return self._model_files
        # 保留原始传输和 HTTP 异常，供上层判定供应商回退。
        resp = await self._client.get("/object_info/UNETLoader")
        resp.raise_for_status()
        unet_opts: list[str] = (
            resp.json().get("UNETLoader", {}).get("input", {}).get("required", {}).get("unet_name", [[]])[0]
        )
        resp = await self._client.get("/object_info/CLIPLoader")
        resp.raise_for_status()
        clip_opts: list[str] = (
            resp.json().get("CLIPLoader", {}).get("input", {}).get("required", {}).get("clip_name", [[]])[0]
        )
        resp = await self._client.get("/object_info/VAELoader")
        resp.raise_for_status()
        vae_opts: list[str] = (
            resp.json().get("VAELoader", {}).get("input", {}).get("required", {}).get("vae_name", [[]])[0]
        )

        def _pick(options: list[str], *prefixes: str) -> str:
            for p in prefixes:
                for name in options:
                    if name.startswith(p):
                        return name
            for name in options:
                if name.startswith("qwen"):
                    return name
            if options:
                return options[0]
            raise ProviderError(
                f"local image_gen found no model for {prefixes}",
                provider=self.provider_name,
                model=self.config.model,
            )

        files = (
            _pick(unet_opts, "qwen_dit", "qwen_image"),
            _pick(clip_opts, "qwen_te", "qwen3vl"),
            _pick(vae_opts, "qwen_vae", "qwen_image_2"),
        )
        self._model_files = files
        logger.info(
            "local image_gen resolved model files",
            extra={"unet": files[0], "clip": files[1], "vae": files[2]},
        )
        return files

    async def _upload_image(self, reference: str) -> str:
        data, mime = await resolve_reference_bytes(reference)
        if len(data) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
            raise ProviderError(
                "local image_gen reference exceeds the configured image size limit",
                status_code=413,
                provider=self.provider_name,
                model=self.config.model,
            )
        ext = "png" if mime == "image/png" else "jpg" if "jpeg" in mime else "webp" if "webp" in mime else "png"
        filename = f"ref_{hashlib.sha256(data).hexdigest()}.{ext}"
        files = {"image": (filename, data, mime)}
        resp = await self._client.post("/upload/image", files=files, data={"overwrite": "true", "type": "input"})
        if resp.status_code >= 400:
            raise ProviderError(
                f"local image_gen upload failed: {resp.status_code} {resp.text[:300]}",
                status_code=resp.status_code,
                body={"text": resp.text[:500]},
                provider=self.provider_name,
                model=self.config.model,
            )
        body = resp.json()
        name = body.get("name") or filename
        sub = body.get("subfolder") or ""
        return f"{sub}/{name}" if sub else name

    async def _wait_and_fetch(self, prompt_id: str, *, require_transparency: bool) -> list[ImageAsset]:
        deadline = time.monotonic() + _JOB_TIMEOUT_S
        history: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            resp = await self._client.get(f"/history/{prompt_id}")
            if resp.status_code >= 400:
                raise ProviderError(
                    f"local image_gen history failed: {resp.status_code}",
                    status_code=resp.status_code,
                    provider=self.provider_name,
                    model=self.config.model,
                )
            payload = resp.json()
            entry = payload.get(prompt_id)
            if entry:
                history = entry
                break
            await asyncio.sleep(_POLL_INTERVAL_S)
        if history is None:
            raise ProviderError(
                f"local image_gen timed out waiting for {prompt_id}",
                provider=self.provider_name,
                model=self.config.model,
            )

        status = history.get("status") or {}
        if status.get("status_str") == "error":
            messages = status.get("messages") or []
            detail = str(messages)[:500]
            raise ProviderError(
                f"local image_gen execution error: {detail}",
                body=status,
                provider=self.provider_name,
                model=self.config.model,
            )

        assets: list[ImageAsset] = []
        for node_out in (history.get("outputs") or {}).values():
            for img in node_out.get("images") or []:
                params = {
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                }
                view = await self._client.get("/view", params=params)
                if view.status_code >= 400:
                    raise ProviderError(
                        f"local image_gen view failed: {view.status_code}",
                        status_code=view.status_code,
                        provider=self.provider_name,
                        model=self.config.model,
                    )
                b64 = base64.b64encode(view.content).decode("utf-8")
                if require_transparency and not _has_transparency(b64):
                    raise ProviderError(
                        "local image_gen returned a PNG without transparent pixels",
                        status_code=400,
                        provider=self.provider_name,
                        model=self.config.model,
                    )
                assets.append(ImageAsset(b64=b64, mime="image/png"))
        return assets

    async def _run_job(self, graph: dict[str, Any], *, require_transparency: bool) -> list[ImageAsset]:
        resp = await self._client.post("/prompt", json={"prompt": graph})
        if resp.status_code >= 400:
            raise ProviderError(
                f"local image_gen submit failed: {resp.status_code} {resp.text[:400]}",
                status_code=resp.status_code,
                body={"text": resp.text[:500]},
                provider=self.provider_name,
                model=self.config.model,
            )
        body = resp.json()
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise ProviderError(
                f"local image_gen missing prompt_id: {body}",
                body=body,
                provider=self.provider_name,
                model=self.config.model,
            )
        assets = await self._wait_and_fetch(prompt_id, require_transparency=require_transparency)
        if not assets:
            raise ProviderError(
                f"local image_gen returned no images for {prompt_id}",
                provider=self.provider_name,
                model=self.config.model,
            )
        return assets

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        unet_name, clip_name, vae_name = await self._resolve_model_files()
        prompt = _wrap_rgba(req.prompt) if req.background == "transparent" else req.prompt
        count = int(req.n or 1)
        if not 1 <= count <= 4:
            raise ProviderError(
                "local image_gen accepts between 1 and 4 images per request",
                status_code=400,
                provider=self.provider_name,
                model=self.config.model,
            )
        require_transparency = req.background == "transparent"

        image_names: list[str] = []
        for ref in (req.reference_image, req.secondary_reference_image):
            if ref:
                image_names.append(await self._upload_image(ref))

        assets: list[ImageAsset] = []
        for i in range(count):
            seed = (int(time.time() * 1000) + i) % (2**31)
            if image_names:
                width, height = _resolve_wh(req)
                graph = _edit_graph(
                    prompt=prompt,
                    seed=seed,
                    image_names=image_names,
                    unet_name=unet_name,
                    clip_name=clip_name,
                    vae_name=vae_name,
                    width=width,
                    height=height,
                    image_edit=bool(req.image_edit),
                )
            else:
                width, height = _resolve_wh(req)
                graph = _t2i_graph(
                    prompt=prompt,
                    width=width,
                    height=height,
                    seed=seed,
                    batch_size=1,
                    unet_name=unet_name,
                    clip_name=clip_name,
                    vae_name=vae_name,
                )
            assets.extend((await self._run_job(graph, require_transparency=require_transparency))[:1])

        return ImageGenResult(images=assets, model=self.config.model, raw={"jobs": count})
