"""本地 ComfyUI 图像生成供应商。ComfyUI 须能从 Backend 访问。"""

import asyncio
import base64
import hashlib
import time
from io import BytesIO
from typing import Any, ClassVar

import httpx
from components import REMOTE_ASSET_DOWNLOAD_MAX_BYTES, get_logger
from PIL import Image

from ...wait_budget import accepted_image_job_wait
from .._reference import resolve_reference_bytes
from ..base import (
    ImageAsset,
    ImageGenProvider,
    ImageGenRequest,
    ImageGenResult,
    ProviderConfig,
    ProviderError,
)
from ..http import ProviderResultUnknownError, get_http

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

_POLL_INTERVAL_S = 30.0
_STEPS = 25


def _wrap_rgba(prompt: str) -> str:
    return _RGBA_WRAP.format(desc=prompt.strip())


def _unet_loader(unet_name: str) -> tuple[str, dict[str, Any]]:
    """ComfyUI-GGUF 的 .gguf 权重只在 UnetLoaderGGUF 列出，原生 UNETLoader 不接受该扩展名。"""
    if unet_name.lower().endswith(".gguf"):
        return "UnetLoaderGGUF", {"unet_name": unet_name}
    return "UNETLoader", {"unet_name": unet_name, "weight_dtype": "default"}


def _image_asset(data: bytes) -> ImageAsset:
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            mime = Image.MIME.get(image.format or "", "image/png")
    except Exception as exc:
        raise ProviderError("local image_gen returned an unreadable image", status_code=400) from exc
    return ImageAsset(b64=base64.b64encode(data).decode("ascii"), mime=mime)


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
    """ComfyUI 生图；透明请求包装提示词，返回可解码图片供消费方校验和处理。"""

    provider_name = "local"
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:8188"
    DEFAULT_MODEL: ClassVar[str] = "qwen"
    requires_api_key: ClassVar[bool] = False  # 本地 ComfyUI 无鉴权；api_key 可为空，仅占位
    supports_reference_image: ClassVar[bool] = True
    supports_environment_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True
    supports_image_edit: ClassVar[bool] = True
    supports_transparent_background: ClassVar[bool] = True
    # Mac 16GB 统一内存不宜并行 batch；单次原生请求只出 1 张，多张由调用方或本层顺序重试
    max_images_per_request: ClassVar[int | None] = 1
    # 本地推理受设备与画幅影响较大；单次 HTTP 仍由 llm_request_timeout_seconds 约束。
    max_job_wait_seconds: ClassVar[float] = 24 * 60 * 60.0

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

    async def _wait_and_fetch(self, prompt_id: str) -> list[ImageAsset]:
        deadline = time.monotonic() + self.max_job_wait_seconds
        history: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            resp = await self._read_job_response(prompt_id, f"/history/{prompt_id}", deadline)
            payload = resp.json()
            entry = payload.get(prompt_id)
            if entry:
                history = entry
                break
            await asyncio.sleep(min(_POLL_INTERVAL_S, max(0.0, deadline - time.monotonic())))
        if history is None:
            raise self._result_unknown(prompt_id, f"waiting exceeded {self.max_job_wait_seconds} seconds")

        status = history.get("status") or {}
        if status.get("status_str") == "error":
            messages = status.get("messages") or []
            detail = str(messages)[:500]
            raise ProviderError(
                f"local image_gen execution error: {detail}",
                status_code=422,
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
                view = await self._read_job_response(prompt_id, "/view", deadline, params=params)
                # 解码检查与编码都是整图字节运算，放到工作线程避免阻塞事件循环
                try:
                    assets.append(await asyncio.to_thread(_image_asset, view.content))
                except ProviderError as exc:
                    raise self._result_unknown(prompt_id, "image output could not be decoded") from exc
        return assets

    async def _read_job_response(
        self,
        prompt_id: str,
        path: str,
        deadline: float,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        while time.monotonic() < deadline:
            try:
                response = await self._client.get(path, params=params)
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if status_code is not None and status_code not in {408, 429} and not 500 <= status_code < 600:
                    raise
                logger.warning(
                    "local image_gen result query failed; retrying same submitted task",
                    extra={"prompt_id": prompt_id, "error_type": type(exc).__name__, "status_code": status_code},
                )
            await asyncio.sleep(min(_POLL_INTERVAL_S, max(0.0, deadline - time.monotonic())))
        raise self._result_unknown(prompt_id, f"waiting exceeded {self.max_job_wait_seconds} seconds")

    def _result_unknown(self, prompt_id: str | None, detail: str) -> ProviderResultUnknownError:
        logger.warning(
            "local image_gen result unavailable after submission",
            extra={"prompt_id": prompt_id, "detail": detail},
        )
        error = ProviderResultUnknownError("POST", self.config.base_url.rstrip("/") + "/prompt")
        error.add_note(f"local image_gen submitted task: {prompt_id or 'handle unavailable'}; {detail}")
        return error

    async def _run_job(self, graph: dict[str, Any]) -> list[ImageAsset]:
        resp = await self._client.post("/prompt", json={"prompt": graph})
        if resp.status_code >= 400:
            raise ProviderError(
                f"local image_gen submit failed: {resp.status_code} {resp.text[:400]}",
                status_code=resp.status_code,
                body={"text": resp.text[:500]},
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise self._result_unknown(None, "successful submission returned unreadable JSON") from exc
        prompt_id = body.get("prompt_id") if isinstance(body, dict) else None
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise self._result_unknown(None, "successful submission returned no valid task handle")
        logger.info("local image_gen task accepted", extra={"prompt_id": prompt_id})
        try:
            with accepted_image_job_wait():
                assets = await self._wait_and_fetch(prompt_id)
        except (ProviderError, ProviderResultUnknownError):
            raise
        except Exception as exc:
            raise self._result_unknown(prompt_id, f"{type(exc).__name__} during result retrieval") from exc
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
            assets.extend((await self._run_job(graph))[:1])

        return ImageGenResult(images=assets)
