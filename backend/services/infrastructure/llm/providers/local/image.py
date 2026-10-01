"""本地 ComfyUI 图像生成供应商。ComfyUI 须能从 Backend 访问。"""

import asyncio
import base64
import hashlib
import time
from io import BytesIO
from typing import Any, ClassVar

from components import REMOTE_ASSET_DOWNLOAD_MAX_BYTES, get_logger
from PIL import Image

from .._reference import resolve_reference_bytes
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

_ASPECT_TO_WH: dict[str, tuple[int, int]] = {  # 画幅短边不低于 1024（与 SIZE_TO_ASPECT 体系一致）
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
# Mac MPS 单张约 10–12 分钟（25 步）；多参考编辑与 2K 更慢。任务总等待按最坏 1 小时预算；单次 HTTP 由 llm_request_timeout_seconds 约束
_JOB_TIMEOUT_S = 3600.0
_STEPS = 25


def _wrap_rgba(prompt: str) -> str:
    return _RGBA_WRAP.format(desc=prompt.strip())


def _unet_loader(unet_name: str) -> tuple[str, dict[str, Any]]:
    """ComfyUI-GGUF 的 .gguf 权重只在 UnetLoaderGGUF 列出，原生 UNETLoader 不接受该扩展名。"""
    if unet_name.lower().endswith(".gguf"):
        return "UnetLoaderGGUF", {"unet_name": unet_name}
    return "UNETLoader", {"unet_name": unet_name, "weight_dtype": "default"}


def _has_transparency(data: bytes) -> bool:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or ("A" not in image.getbands() and "transparency" not in image.info):
                return False
            low, high = image.convert("RGBA").getchannel("A").getextrema()
            return low < 255 and high > 0
    except Exception:
        return False


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _resolve_wh(req: ImageGenRequest) -> tuple[int, int]:
    if req.size and "x" in req.size.lower():
        try:
            w_str, h_str = req.size.lower().split("x", 1)
            w, h = int(w_str), int(h_str)
            if w > 0 and h > 0:
                return (max(64, w // 32 * 32), max(64, h // 32 * 32))
        except ValueError:
            pass
    return _ASPECT_TO_WH.get(req.aspect_ratio or "", (1024, 1024))


def _graph(
    *,
    prompt: str,
    seed: int,
    width: int,
    height: int,
    image_names: list[str],
    image_edit: bool,
    unet_name: str,
    clip_name: str,
    vae_name: str,
) -> dict[str, Any]:
    """ComfyUI 工作流；有参考图时由编码节点读取参考图。image_edit=True（须带参考图）采样 latent 取编码节点输出、画布随第一张参考图，否则用 width×height 的 EmptyLatentImage。"""
    encode_inputs: dict[str, Any] = {"clip": ["2", 0]}
    if image_names:
        encode_inputs["vae"] = ["3", 0]
    encode_inputs.update(
        prompt=prompt,
        negative_prompt="",
        resolution=0 if image_names else min(width, height),
    )
    unet_class, unet_inputs = _unet_loader(unet_name)
    nodes: dict[str, Any] = {
        "1": {
            "class_type": unet_class,
            "inputs": unet_inputs,
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": clip_name, "type": "qwen_image", "device": "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "4": {"class_type": "TextEncodeQwenImage21", "inputs": encode_inputs},
    }
    if not image_edit:
        nodes["5"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        }
    nodes.update(
        {
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["4", 1],
                    "latent_image": ["4", 2] if image_edit else ["5", 0],
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
        },
    )
    for i, name in enumerate(image_names, start=1):
        load_id = f"1{i}"
        nodes[load_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        encode_inputs[f"images.image_{i}"] = [load_id, 0]
    return nodes


class LocalImageGenProvider(ImageGenProvider):
    """ComfyUI 生图；默认 base_url 指向 localhost。透明请求包装提示词，返回前核验 PNG 含可见透明像素。"""

    provider_name = "local"
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:8188"
    DEFAULT_MODEL: ClassVar[str] = "qwen"
    requires_api_key: ClassVar[bool] = False  # 本地 ComfyUI 无鉴权；api_key 可为空，仅占位
    supports_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True
    supports_image_edit: ClassVar[bool] = True
    supports_transparent_background: ClassVar[bool] = True
    # Mac 16GB 统一内存不宜并行 batch；单次原生请求只出 1 张，多张由调用方或本层顺序重试
    max_images_per_request: ClassVar[int | None] = 1

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        # ComfyUI 不校验 Authorization；空 auth_header 避免带 Bearer 占位。单次请求超时仍远小于任务总预算
        self._client = get_http(config.base_url, config.api_key or "local", auth_header={})
        self._model_files: tuple[str, str, str] | None = None

    async def _resolve_model_files(self) -> tuple[str, str, str]:
        if self._model_files is not None:
            return self._model_files
        # 保留原始传输和 HTTP 异常，供上层判定供应商回退
        resp = await self._client.get("/object_info/UNETLoader")
        resp.raise_for_status()
        unet_opts: list[str] = (
            resp.json().get("UNETLoader", {}).get("input", {}).get("required", {}).get("unet_name", [[]])[0]
        )
        # .gguf 权重由 ComfyUI-GGUF 的 UnetLoaderGGUF 单独列出；节点未安装时跳过
        gguf_opts: list[str] = []
        resp = await self._client.get("/object_info/UnetLoaderGGUF")
        if resp.status_code < 400:
            gguf_opts = (
                resp.json().get("UnetLoaderGGUF", {}).get("input", {}).get("required", {}).get("unet_name", [[]])[0]
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
            )

        files = (
            _pick([*unet_opts, *gguf_opts], "qwen_dit", "qwen_image"),
            _pick(clip_opts, "qwen_te", "qwen3vl"),
            _pick(vae_opts, "qwen_vae", "qwen_image_2"),
        )
        self._model_files = files
        unet_class, _ = _unet_loader(files[0])
        logger.info(
            "local image_gen resolved model files",
            extra={"unet": files[0], "unet_loader": unet_class, "clip": files[1], "vae": files[2]},
        )
        return files

    async def _upload_image(self, reference: str) -> str:
        data, mime = await resolve_reference_bytes(reference)
        if len(data) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
            raise ProviderError(
                "local image_gen reference exceeds the configured image size limit",
                status_code=413,
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
            )

        status = history.get("status") or {}
        if status.get("status_str") == "error":
            messages = status.get("messages") or []
            detail = str(messages)[:500]
            raise ProviderError(
                f"local image_gen execution error: {detail}",
                body=status,
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
                    )
                # 解码检查与编码都是整图字节运算，放到工作线程避免阻塞事件循环
                if require_transparency and not await asyncio.to_thread(_has_transparency, view.content):
                    raise ProviderError(
                        "local image_gen returned a PNG without transparent pixels",
                        status_code=400,
                    )
                b64 = await asyncio.to_thread(_b64encode, view.content)
                assets.append(ImageAsset(b64=b64, mime="image/png"))
        return assets

    async def _run_job(self, graph: dict[str, Any], *, require_transparency: bool) -> list[ImageAsset]:
        resp = await self._client.post("/prompt", json={"prompt": graph})
        if resp.status_code >= 400:
            raise ProviderError(
                f"local image_gen submit failed: {resp.status_code} {resp.text[:400]}",
                status_code=resp.status_code,
                body={"text": resp.text[:500]},
            )
        body = resp.json()
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise ProviderError(
                f"local image_gen missing prompt_id: {body}",
                body=body,
            )
        assets = await self._wait_and_fetch(prompt_id, require_transparency=require_transparency)
        if not assets:
            raise ProviderError(
                f"local image_gen returned no images for {prompt_id}",
            )
        return assets

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        unet_name, clip_name, vae_name = await self._resolve_model_files()
        require_transparency = req.background == "transparent"
        prompt = _wrap_rgba(req.prompt) if require_transparency else req.prompt
        count = int(req.n or 1)
        if not 1 <= count <= 4:
            raise ProviderError("local image_gen accepts between 1 and 4 images per request", status_code=400)

        image_names: list[str] = []
        for ref in (req.reference_image, req.secondary_reference_image):
            if ref:
                image_names.append(await self._upload_image(ref))
        width, height = _resolve_wh(req)

        assets: list[ImageAsset] = []
        for i in range(count):
            graph = _graph(
                prompt=prompt,
                seed=(int(time.time() * 1000) + i) % (2**31),
                width=width,
                height=height,
                image_names=image_names,
                image_edit=bool(req.image_edit and image_names),
                unet_name=unet_name,
                clip_name=clip_name,
                vae_name=vae_name,
            )
            assets.extend((await self._run_job(graph, require_transparency=require_transparency))[:1])

        return ImageGenResult(images=assets)
