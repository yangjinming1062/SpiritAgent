"""本地 ComfyUI 图像生成供应商，固定使用 Qwen-Image-2.1-Turbo 工作流。ComfyUI 须能从 Backend 访问。"""

import asyncio
import base64
import hashlib
import time
from io import BytesIO
from typing import Any, ClassVar
from uuid import uuid4

import httpx
from PIL import Image

from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.ai.providers.base import (
    ImageAsset,
    ImageGenProvider,
    ImageGenRequest,
    ImageGenResult,
    ProviderConfig,
    ProviderError,
)
from infrastructure.ai.providers.http import ProviderResultUnknownError, get_http
from infrastructure.async_io import cancel_and_wait, settle_task
from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.logger import get_logger

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

_DEFAULT_WH = (1024, 1024)
_POLL_INTERVAL_S = 5.0
_CLIP_TYPE = "qwen_image"
# Qwen-Image Turbo 的蒸馏采样档：8 步 sigma 表，euler。
_TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0"
# 权重前缀按具体程度排列；模型卡片名称只作展示，不参与选文件。
_UNET_PREFIXES = ("qwen_image_2.1_turbo", "qwen_image_2.1", "qwen")
_CLIP_PREFIXES = ("qwen3vl_8b_abliterated", "qwen3vl_8b", "qwen_image")
_VAE_PREFIXES = ("qwen_image_2.1_vae", "qwen_image_vae", "qwen")


def _wrap_rgba(prompt: str) -> str:
    return _RGBA_WRAP.format(desc=prompt.strip())


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
    return _ASPECT_TO_WH.get(req.aspect_ratio or "", _DEFAULT_WH)


def _pick_weights(options: list[str], prefixes: tuple[str, ...]) -> str:
    """按权重命名前缀选文件；都不匹配时直接报错，避免把同目录下的其它模型装进本工作流。"""
    for prefix in prefixes:
        for name in options:
            if name.startswith(prefix):
                return name
    raise ProviderError(f"local image_gen found no model for {prefixes}")


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
    """Qwen-Image-2.1-Turbo 工作流；参考图进编码节点，编辑时以其 latent 为画布。"""
    encode: dict[str, Any] = {"clip": ["2", 0], "prompt": prompt, "negative_prompt": ""}
    if image_names:
        encode["vae"] = ["3", 0]
    encode["resolution"] = 0 if image_names else min(width, height)
    nodes: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet_name, "weight_dtype": "default"}},
        "2": {
            "class_type": "CLIPLoader",
            # 编码器始终留 GPU：参考图的视觉分支会走 conv3d，而 ComfyUI 在 RDNA3+ 上默认关闭 MIOpen，
            # 关闭态的 conv3d 在此 ROCm 栈上堆损坏崩服；服务端以 COMFYUI_ENABLE_MIOPEN=1 保留 MIOpen。
            "inputs": {"clip_name": clip_name, "type": _CLIP_TYPE, "device": "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "4": {"class_type": "TextEncodeQwenImage21", "inputs": encode},
        "6": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "7": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["4", 0]}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "9": {"class_type": "ManualSigmas", "inputs": {"sigmas": _TURBO_SIGMAS}},
        "10": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["6", 0],
                "guider": ["7", 0],
                "sampler": ["8", 0],
                "sigmas": ["9", 0],
                "latent_image": ["4", 2] if image_edit else ["5", 0],
            },
        },
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
        "12": {"class_type": "PreviewImage", "inputs": {"images": ["11", 0]}},
    }
    if not image_edit:
        nodes["5"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
    for i, name in enumerate(image_names, start=1):
        load_id = str(20 + i)
        nodes[load_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        encode[f"images.image_{i}"] = [load_id, 0]
    return nodes


class LocalImageGenProvider(ImageGenProvider):
    """ComfyUI 生图；返回可解码图片供消费方校验和处理。"""

    provider_name = "local"
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:8188"
    requires_api_key: ClassVar[bool] = False  # 本地 ComfyUI 无鉴权；api_key 可为空，仅占位
    supports_reference_image: ClassVar[bool] = True
    supports_environment_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True
    supports_image_edit: ClassVar[bool] = True
    # Qwen 的 VAE 会出 alpha，透明请求用提示词索取通道；消费方仍按实际 alpha 验收。
    supports_transparent_background: ClassVar[bool] = True
    # 单次原生请求只出 1 张，多张由调用方或本层顺序重试
    max_images_per_request: ClassVar[int | None] = 1

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        # ComfyUI 不校验 Authorization；空 auth_header 避免带 Bearer 占位。
        self._client = get_http(config.base_url, config.api_key or "local", auth_header={})
        self._model_files: tuple[str, str, str] | None = None

    async def _loader_options(self, node_name: str, field: str) -> list[str]:
        resp = await self._client.get(f"/object_info/{node_name}")
        resp.raise_for_status()
        options = resp.json().get(node_name, {}).get("input", {}).get("required", {}).get(field, [[]])
        return (
            [name for name in options[0] if isinstance(name, str)] if options and isinstance(options[0], list) else []
        )

    async def _resolve_model_files(self) -> tuple[str, str, str]:
        if self._model_files is not None:
            return self._model_files
        # 保留原始传输和 HTTP 异常，供上层判定供应商回退
        files = (
            _pick_weights(await self._loader_options("UNETLoader", "unet_name"), _UNET_PREFIXES),
            _pick_weights(await self._loader_options("CLIPLoader", "clip_name"), _CLIP_PREFIXES),
            _pick_weights(await self._loader_options("VAELoader", "vae_name"), _VAE_PREFIXES),
        )
        self._model_files = files
        logger.info("local image_gen resolved model files", extra={"unet": files[0], "clip": files[1], "vae": files[2]})
        return files

    async def _upload_image(self, reference: str) -> str:
        data, mime = await resolve_reference_bytes(reference)
        if len(data) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
            raise ProviderError("local image_gen reference exceeds the configured image size limit", status_code=413)
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
        history: dict[str, Any] | None = None
        while history is None:
            resp = await self._read_job_response(prompt_id, f"/history/{prompt_id}")
            payload = resp.json()
            entry = payload.get(prompt_id)
            if entry and (
                entry.get("status", {}).get("completed")
                or entry.get("status", {}).get("status_str") in {"success", "error"}
            ):
                history = entry
                break
            await asyncio.sleep(_POLL_INTERVAL_S)

        status = history.get("status") or {}
        if status.get("status_str") == "error":
            messages = status.get("messages") or []
            detail = str(messages)[:500]
            raise ProviderError(f"local image_gen execution error: {detail}", status_code=422, body=status)

        assets: list[ImageAsset] = []
        for node_out in (history.get("outputs") or {}).values():
            for img in node_out.get("images") or []:
                params = {
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                }
                view = await self._read_job_response(prompt_id, "/view", params=params)
                # 解码检查与编码都是整图字节运算，放到工作线程避免阻塞事件循环
                try:
                    assets.append(await asyncio.to_thread(_image_asset, view.content))
                except ProviderError as exc:
                    raise self._result_unknown(prompt_id, "image output could not be decoded") from exc
        return assets

    async def _read_job_response(
        self, prompt_id: str, path: str, *, params: dict[str, str] | None = None
    ) -> httpx.Response:
        while True:
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
            await asyncio.sleep(_POLL_INTERVAL_S)

    def _result_unknown(self, prompt_id: str | None, detail: str) -> ProviderResultUnknownError:
        logger.warning(
            "local image_gen result unavailable after submission", extra={"prompt_id": prompt_id, "detail": detail}
        )
        error = ProviderResultUnknownError("POST", self.config.base_url.rstrip("/") + "/prompt")
        error.add_note(f"local image_gen submitted task: {prompt_id or 'handle unavailable'}; {detail}")
        return error

    async def _run_job(self, graph: dict[str, Any]) -> list[ImageAsset]:
        request_id = str(uuid4())
        submission = asyncio.create_task(self._client.post("/prompt", json={"prompt": graph, "prompt_id": request_id}))
        try:
            resp = await asyncio.shield(submission)
        except asyncio.CancelledError:
            await settle_task(asyncio.create_task(self._cancel_submission(submission, request_id)))
            raise
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
            assets = await self._wait_and_fetch(prompt_id)
        except asyncio.CancelledError:
            # 只操作本次 prompt；服务未提供定向取消时仅移除本任务的排队项。
            await settle_task(asyncio.create_task(self._cancel_job(prompt_id)))
            raise
        except (ProviderError, ProviderResultUnknownError):
            raise
        except Exception as exc:
            raise self._result_unknown(prompt_id, f"{type(exc).__name__} during result retrieval") from exc
        if not assets:
            raise ProviderError(f"local image_gen returned no images for {prompt_id}")
        return assets

    async def _cancel_submission(self, submission: asyncio.Task[httpx.Response], request_id: str) -> None:
        prompt_id = request_id
        try:
            async with asyncio.timeout(15):
                response = await asyncio.shield(submission)
            if response.is_success:
                body = response.json()
                returned_id = body.get("prompt_id") if isinstance(body, dict) else None
                if isinstance(returned_id, str) and returned_id:
                    prompt_id = returned_id
        except Exception:
            logger.warning(
                "Local image submission handle was not confirmed", extra={"prompt_id": request_id}, exc_info=True
            )
        finally:
            await cancel_and_wait((submission,))
        await self._cancel_job(prompt_id)

    async def _cancel_job(self, prompt_id: str) -> None:
        try:
            async with asyncio.timeout(10):
                response = await self._client.post(f"/api/jobs/{prompt_id}/cancel", json={})
                if response.status_code in (404, 405):
                    response = await self._client.post("/queue", json={"delete": [prompt_id]})
                    response.raise_for_status()
                    logger.info("Local image queue cancellation requested", extra={"prompt_id": prompt_id})
                    return
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or type(body.get("cancelled")) is not bool:
                    raise ValueError("Local image cancellation response is invalid")
                logger.info(
                    "Local image cancellation completed", extra={"prompt_id": prompt_id, "cancelled": body["cancelled"]}
                )
        except Exception:
            logger.warning("Local image cancellation was not confirmed", extra={"prompt_id": prompt_id}, exc_info=True)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        unet_name, clip_name, vae_name = await self._resolve_model_files()
        prompt = _wrap_rgba(req.prompt) if req.background == "transparent" else req.prompt
        count = int(req.n or 1)
        if not 1 <= count <= 4:
            raise ProviderError("local image_gen accepts between 1 and 4 images per request", status_code=400)

        image_names = [
            await self._upload_image(ref)
            for ref in (req.reference_image, req.secondary_reference_image, req.environment_reference_image)
            if ref
        ]
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
