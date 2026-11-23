"""本地 ComfyUI MiniMax H3 视频供应商。

该适配器使用 ComfyUI 标准 ``/prompt``、``/history`` 和 ``/view`` 接口，
固定装配已在本地部署验证过的 MiniMax H3 工作流。支持文生视频、首帧/首尾帧、参考图和参考视频。
"""

import asyncio
import hashlib
import math
from dataclasses import replace
from io import BytesIO
from typing import Any, ClassVar, Literal
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from PIL import Image, ImageOps

from infrastructure.ai.media_guidance import CHARACTER_FACE_MOTION
from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.ai.providers.base import (
    ProviderConfig,
    ProviderError,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobStatus,
)
from infrastructure.ai.providers.http import ProviderResultUnknownError, get_http
from infrastructure.ai.providers.local.comfyui import loader_options, supports_nodes
from infrastructure.ai.providers.local.image import (
    POSE_PLANNING_NODES,
    qwen_face_anchor,
    qwen_image_weights,
    qwen_opening_nodes,
)
from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.logger import get_logger

logger = get_logger(__name__)

_MAX_PROMPT_CHARS = 7000
_MAX_REFERENCE_VIDEO_BYTES = 50 * 1024 * 1024
_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
_ASPECT_RATIO_RANGE = (1 / 8, 8.0)
_H3_WIDESCREEN_SIZES = {"512P": (896, 512), "768P": (1344, 768)}
_UPSCALE_MODEL = "RealESRGAN_x2plus.pth"
_DELIVERY_MAX_EDGE = 2048
_UNET_PREFIXES = ("minimax_h3_fl2va_pruned_int8_convrot", "minimax_h3_fl2va")
_REFERENCE_UNETS = ("minimax_h3_ref2va_pruned_int8_convrot", "minimax_h3_ref2va")
_REFERENCE_LORAS = (("minimax_h3_ref2v_turbo_4step", 4), ("minimax_h3_ref2v_turbo_8step", 8))
_CLIP_PREFIXES = ("qwen3vl_32b_minimax_h3_int8_convrot", "MiniMax-H3-TextEncoder")
_VIDEO_VAE_PREFIXES = ("minimax_h3_video_vae",)
_AUDIO_VAE_PREFIXES = ("minimax_h3_audio_vae",)
_LORA_PREFIXES = (
    ("minimax_h3_fl2v_turbo_4step", 4),
    ("minimax_h3_fl2v_turbo_8step", 8),
)


def _pick(options: list[str], prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        for name in options:
            if name.startswith(prefix):
                return name
    raise ProviderError(f"local video_gen found no model for {prefixes}", status_code=422)


def _h3_weights(
    unets: list[str], clips: list[str], vaes: list[str], loras: list[str], *, native: bool, detail: bool
) -> tuple[str, str, str, str, str, int]:
    variants = _REFERENCE_LORAS if native else _LORA_PREFIXES
    if detail:
        variants = tuple(reversed(variants))
    lora = _pick(loras, tuple(prefix for prefix, _ in variants))
    return (
        _pick(unets, _REFERENCE_UNETS if native else _UNET_PREFIXES),
        _pick(clips, _CLIP_PREFIXES),
        _pick(vaes, _VIDEO_VAE_PREFIXES),
        _pick(vaes, _AUDIO_VAE_PREFIXES),
        lora,
        next(count for prefix, count in variants if lora.startswith(prefix)),
    )


def local_video_frame_count(duration: int) -> int:
    """复现 H3 工作流的 ``max(5, round(seconds * 24))`` 帧网格表达式。"""
    target = max(5, round(duration * 24))
    return target + (5 - target % 17) % 17


def _size(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    long_edge, short_edge = _H3_WIDESCREEN_SIZES[resolution]
    ratio = _aspect_value(aspect_ratio)
    height = min(short_edge / min(ratio, 1), long_edge / max(ratio, 1))
    width, height = (max(32, round(value / 32) * 32) for value in (ratio * height, height))
    return width, height


def _aspect_value(aspect_ratio: str) -> float:
    try:
        ratio_w, ratio_h = (float(value) for value in aspect_ratio.split(":"))
    except (ValueError, AttributeError) as exc:
        raise ProviderError(f"local video_gen does not support aspect ratio {aspect_ratio!r}", status_code=400) from exc
    if not all(math.isfinite(value) and value > 0 for value in (ratio_w, ratio_h)):
        raise ProviderError(f"local video_gen does not support aspect ratio {aspect_ratio!r}", status_code=400)
    ratio = ratio_w / ratio_h
    lower, upper = _ASPECT_RATIO_RANGE
    if not lower <= ratio <= upper:
        raise ProviderError("local video_gen aspect ratio must be between 1:8 and 8:1", status_code=400)
    return ratio


def _add_upscale(nodes: dict[str, dict[str, Any]], width: int, height: int) -> None:
    nodes["50"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": _UPSCALE_MODEL}}
    nodes["51"] = {
        "class_type": "ImageUpscaleWithModel",
        "inputs": {"image": ["12", 0], "upscale_model": ["50", 0]},
    }
    output = "51"
    scale = _DELIVERY_MAX_EDGE / max(width, height)
    if scale < 2:
        nodes["52"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": [output, 0],
                "upscale_method": "lanczos",
                "width": round(width * scale / 2) * 2,
                "height": round(height * scale / 2) * 2,
                "crop": "disabled",
            },
        }
        output = "52"
    nodes["14"]["inputs"]["images"] = [output, 0]
    # 在透明处理前保留颜色与边缘细节，避免对同一视频做低质量的有损中转。
    save = nodes["15"]["inputs"]
    del save["codec"]
    save.update(
        {"format": "mp4", "format.codec": "h264", "format.codec.encoding": "re-encode", "format.codec.encoding.crf": 16}
    )


def _view_url(base_url: str, item: dict[str, Any]) -> str | None:
    filename = item.get("filename")
    if not isinstance(filename, str) or not filename:
        return None
    subfolder = item.get("subfolder") or ""
    kind = item.get("type") or "output"
    if not isinstance(subfolder, str) or not isinstance(kind, str):
        return None
    return base_url.rstrip("/") + "/view?" + urlencode({"filename": filename, "subfolder": subfolder, "type": kind})


async def _adaptive_request(req: VideoGenRequest) -> VideoGenRequest:
    """从首帧读取自适应画幅，原生尺寸由 H3 的网格对齐。"""
    if req.aspect_ratio is not None or not req.first_frame_image:
        return req if req.aspect_ratio is not None else replace(req, aspect_ratio="16:9")
    data, _mime = await resolve_reference_bytes(req.first_frame_image)
    with Image.open(BytesIO(data)) as image:
        ImageOps.exif_transpose(image, in_place=True)
        width, height = image.size
    return replace(req, aspect_ratio=f"{width}:{height}")


class LocalVideoGenProvider(VideoGenProvider):
    """通过 ComfyUI 的 MiniMax H3 工作流生成文生视频。"""

    provider_name = "local"
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:8188"
    requires_api_key: ClassVar[bool] = False
    durations: ClassVar[tuple[int, ...]] = tuple(range(4, 16))
    resolutions: ClassVar[tuple[str, ...]] = tuple(_H3_WIDESCREEN_SIZES)
    supports_first_frame: ClassVar[bool] = True
    supports_loop_frames: ClassVar[bool] = True
    supports_reference_images: ClassVar[bool] = True
    max_reference_images: ClassVar[int | None] = 9
    supports_reference_videos: ClassVar[bool] = True
    max_reference_videos: ClassVar[int | None] = 3
    supports_adaptive_aspect_ratio: ClassVar[bool] = True
    aspect_ratios: ClassVar[tuple[str, ...]] = _ASPECT_RATIOS
    adaptive_aspect_ratio_range: ClassVar[tuple[float, float]] = _ASPECT_RATIO_RANGE

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key or "local", auth_header={})

    async def _model_options(self) -> tuple[list[str], list[str], list[str], list[str]]:
        unets, clips, vaes, loras = await asyncio.gather(
            loader_options(self._client, "UNETLoader", "unet_name"),
            loader_options(self._client, "CLIPLoader", "clip_name"),
            loader_options(self._client, "VAELoader", "vae_name"),
            loader_options(self._client, "LoraLoaderModelOnly", "lora_name"),
        )
        return unets, clips, vaes, loras

    async def supports_face_refinement(self) -> bool:
        """导引图和连续运动均可制作时才启用这次底片的可选精修。"""
        if not self.supports_reference_images:
            return False
        try:
            unets, clips, vaes, loras = await self._model_options()
            qwen_image_weights(unets, clips, vaes)
            _h3_weights(unets, clips, vaes, loras, native=False, detail=True)
        except (httpx.HTTPError, ValueError, ProviderError):
            logger.info("Local face refinement weights unavailable", exc_info=True)
            return False
        return await supports_nodes(
            self._client,
            "TextEncodeQwenImage21",
            "ManualSigmas",
            "SplitSigmas",
            "EmptyImage",
            "ImageCompositeMasked",
            "MiniMaxH3ImageToVideo",
            "MiniMaxH3AddGuide",
        )

    async def _resolve_model_files(
        self, req: VideoGenRequest
    ) -> tuple[tuple[str, str, str, str, str, int], tuple[str, str, str] | None]:
        unets, clips, vaes, loras = await self._model_options()
        references = bool(req.reference_images or req.reference_videos)
        native = (
            references
            and not req.face_guide_frames
            and any(name.startswith(_REFERENCE_UNETS) for name in unets)
            and any(name.startswith(prefix) for prefix, _ in _REFERENCE_LORAS for name in loras)
        )
        if req.reference_videos and not native:
            raise ProviderError("参考视频需要 MiniMax H3 Ref2VA 和匹配的 Ref2V Turbo 权重", status_code=422)
        opening = (
            qwen_image_weights(unets, clips, vaes) if references and not native and req.face_stage != "motion" else None
        )
        if not references and req.opening_frame_prompt and not (req.first_frame_image or req.last_frame_image):
            try:
                opening = qwen_image_weights(unets, clips, vaes)
            except ProviderError:
                # 纯文字视频的纠偏仍可由 H3 直接制作。
                opening = None
        files = _h3_weights(unets, clips, vaes, loras, native=native, detail=bool(req.face_stage))
        unet, clip, vae, audio_vae, lora, steps = files
        logger.info(
            "local video_gen resolved model files",
            extra={"unet": unet, "clip": clip, "video_vae": vae, "audio_vae": audio_vae, "lora": lora},
        )
        return files, opening

    def available_resolutions(
        self,
        *,
        duration: int,
        first_frame: bool = False,
        last_frame: bool = False,
        reference_images: bool = False,
        reference_videos: bool = False,
        include_upscaled: bool = False,
    ) -> tuple[str, ...]:
        if duration not in self.durations:
            return ()
        return self.resolutions

    def resolution_size(self, resolution: str, aspect_ratio: str) -> tuple[int, int]:
        native = self.native_resolution(resolution)
        if native is None:
            raise ValueError(f"local video_gen does not support {resolution!r} at {aspect_ratio!r}")
        return _size(native, aspect_ratio)

    def validate_request(self, req: VideoGenRequest) -> None:
        super().validate_request(req)
        if req.noise_seed is not None and (type(req.noise_seed) is not int or not 0 <= req.noise_seed < 2**64):
            raise ProviderError("local video_gen requires an unsigned 64-bit seed", status_code=400)
        if bool(req.face_guide_frames) != bool(req.face_stage):
            raise ProviderError("面部制作阶段与导引时间轴不匹配", status_code=400)
        if req.face_guide_frames:
            times = req.face_guide_frames
            if (
                len(times) < 2
                or len(times) + int(req.face_stage == "anchors") != len(req.reference_images)
                or times[0] != 0
                or times[-1] != local_video_frame_count(req.duration) - 1
                or times != tuple(sorted(set(times)))
                or req.resolution != "768P"
                or req.aspect_ratio != "1:1"
                or req.reference_videos
            ):
                raise ProviderError("面部导引图与原片时间轴不一致", status_code=400)
        if len(req.reference_images) > 9:
            raise ProviderError("local video_gen accepts at most nine reference images", status_code=400)
        if len(req.reference_videos) > 3:
            raise ProviderError("local video_gen accepts at most three reference videos", status_code=400)
        if (req.reference_images or req.reference_videos) and (req.first_frame_image or req.last_frame_image):
            raise ProviderError("local video_gen cannot mix reference media with first/last frames", status_code=400)
        if not req.prompt.strip() or len(req.prompt) > _MAX_PROMPT_CHARS:
            raise ProviderError(
                f"local video_gen prompt must contain 1..{_MAX_PROMPT_CHARS} characters", status_code=400
            )
        if type(req.duration) is not int or req.duration not in self.durations:
            raise ProviderError("local video_gen duration must be between 4 and 15 seconds", status_code=400)
        if self.native_resolution(req.resolution) is None:
            raise ProviderError(f"local video_gen resolution must be one of {self.resolutions}", status_code=400)
        if req.aspect_ratio is not None:
            _aspect_value(req.aspect_ratio)

    async def _upload_image(self, reference: str) -> str:
        data, mime = await resolve_reference_bytes(reference)
        if len(data) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
            raise ProviderError("local video_gen reference exceeds the configured image size limit", status_code=413)
        ext = "png" if mime == "image/png" else "jpg" if "jpeg" in mime else "webp" if "webp" in mime else "png"
        filename = f"ref_{hashlib.sha256(data).hexdigest()}.{ext}"
        response = await self._client.post(
            "/upload/image",
            files={"image": (filename, data, mime)},
            data={"overwrite": "true", "type": "input"},
        )
        if response.status_code >= 400:
            raise ProviderError(
                f"local video_gen image upload failed: {response.status_code} {response.text[:300]}",
                status_code=response.status_code,
                body={"text": response.text[:500]},
            )
        body = response.json()
        name = body.get("name") or filename
        subfolder = body.get("subfolder") or ""
        return f"{subfolder}/{name}" if subfolder else name

    async def _upload_video(self, reference: str) -> str:
        data, mime = await resolve_reference_bytes(reference)
        if len(data) > _MAX_REFERENCE_VIDEO_BYTES:
            raise ProviderError("local video_gen reference video exceeds the configured size limit", status_code=413)
        if data[4:8] == b"ftyp":
            mime = "video/mp4"
        ext = "mp4" if mime == "video/mp4" else "mov" if mime == "video/quicktime" else "webm"
        filename = f"ref_{hashlib.sha256(data).hexdigest()}.{ext}"
        response = await self._client.post(
            "/upload/image",
            files={"image": (filename, data, mime)},
            data={"overwrite": "true", "type": "input"},
        )
        if response.status_code >= 400:
            raise ProviderError(
                f"local video_gen reference video upload failed: {response.status_code} {response.text[:300]}",
                status_code=response.status_code,
                body={"text": response.text[:500]},
            )
        body = response.json()
        name = body.get("name") or filename
        subfolder = body.get("subfolder") or ""
        return f"{subfolder}/{name}" if subfolder else name

    def _graph(
        self,
        req: VideoGenRequest,
        files: tuple[str, str, str, str, str, int],
        *,
        first_frame_name: str | None = None,
        last_frame_name: str | None = None,
        reference_image_names: tuple[str, ...] = (),
        reference_video_names: tuple[str, ...] = (),
        opening_files: tuple[str, str, str] | None = None,
        hold_names: tuple[str, str] | None = None,
        upscale: bool = False,
        plan_pose: bool = False,
    ) -> dict[str, dict[str, Any]]:
        unet, clip, video_vae, audio_vae, lora, steps = files
        width, height = self.resolution_size(req.resolution, req.aspect_ratio or "16:9")
        length = local_video_frame_count(req.duration)
        seed = req.noise_seed if req.noise_seed is not None else uuid4().int % (2**63)
        nodes: dict[str, dict[str, Any]] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "2": {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {"model": ["1", 0], "lora_name": lora, "strength_model": 1.0},
            },
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "minimax"}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": video_vae}},
            "5": {"class_type": "VAELoader", "inputs": {"vae_name": audio_vae}},
            "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {}},
            "7": {"class_type": "BasicGuider", "inputs": {"model": ["2", 0], "conditioning": ["6", 0]}},
            "8": {
                "class_type": "BasicScheduler",
                "inputs": {"model": ["2", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0},
            },
            "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "11": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["10", 0],
                    "guider": ["7", 0],
                    "sampler": ["9", 0],
                    "sigmas": ["8", 0],
                    "latent_image": ["6", 1],
                },
            },
            "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
            "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["5", 0]}},
            "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "audio": ["13", 0], "fps": 24}},
            "15": {
                "class_type": "SaveVideo",
                "inputs": {
                    "video": ["14", 0],
                    "filename_prefix": f"video/spiritagent-{uuid4().hex}",
                    "format": "auto",
                    "codec": "auto",
                },
            },
        }
        if req.face_stage == "anchors":
            if not opening_files:
                raise ProviderError("局部精修需要原始面容及本地图片模型", status_code=422)
            # Qwen 与 H3 分别运行；无损导引图写入任务资产后才开始连续运动作业。
            stills = {
                "atlas": {
                    "class_type": "EmptyImage",
                    "inputs": {"width": 768 * len(req.face_guide_frames), "height": 768, "batch_size": 1, "color": 0},
                }
            }
            canvas = ["atlas", 0]
            for column, (index, name) in enumerate(zip(req.face_guide_frames, reference_image_names[1:], strict=True)):
                guide, output = qwen_face_anchor(
                    name,
                    reference_image_names[0],
                    opening_files,
                    seed=(seed + index) % 2**64,
                    prefix=f"face_{index}_",
                    describe=plan_pose,
                    share_weights=column > 0,
                    repair_observation=req.face_repair_observation,
                )
                stills.update(guide)
                key = f"place_{index}"
                stills[key] = {
                    "class_type": "ImageCompositeMasked",
                    "inputs": {
                        "destination": canvas,
                        "source": output,
                        "x": 768 * column,
                        "y": 0,
                        "resize_source": False,
                    },
                }
                canvas = [key, 0]
            stills["15"] = {
                "class_type": "SaveImage",
                "inputs": {"images": canvas, "filename_prefix": f"video/spiritagent-guides-{uuid4().hex}"},
            }
            return stills
        if req.face_stage == "motion":
            anchors = []
            for index, name in zip(req.face_guide_frames, reference_image_names, strict=True):
                key = f"face_{index}"
                nodes[key] = {"class_type": "LoadImage", "inputs": {"image": name}}
                anchors.append([key, 0])
            nodes["6"]["inputs"].update(
                clip=["3", 0],
                vae=["4", 0],
                prompt=req.prompt,
                width=width,
                height=height,
                length=length,
                first_frame=anchors[0],
                last_frame=anchors[-1],
            )
            conditioning = ["6", 0]
            for index, anchor in zip(req.face_guide_frames[1:-1], anchors[1:-1], strict=True):
                key = f"guide_{index}"
                nodes[key] = {
                    "class_type": "MiniMaxH3AddGuide",
                    "inputs": {
                        "positive": conditioning,
                        "latent": ["6", 1],
                        "vae": ["4", 0],
                        "image": anchor,
                        "frame_idx": index,
                    },
                }
                conditioning = [key, 0]
            nodes["7"]["inputs"]["conditioning"] = conditioning
            nodes["15"]["inputs"] = {
                "video": ["14", 0],
                "filename_prefix": nodes["15"]["inputs"]["filename_prefix"],
                "format": "mp4",
                "format.codec": "h264",
                "format.codec.encoding": "re-encode",
                "format.codec.encoding.crf": 14,
            }
            return nodes
        if opening_files is None and (reference_image_names or reference_video_names):
            nodes["6"] = {
                "class_type": "MiniMaxH3ReferenceToVideo",
                "inputs": {
                    "clip": ["3", 0],
                    "vae": ["4", 0],
                    "audio_vae": ["5", 0],
                    "prompt": " ".join(
                        [f"图 {index}：<Picture {index}>。" for index in range(1, len(reference_image_names) + 1)]
                        + [f"视频 {index}：<Video {index}>。" for index in range(1, len(reference_video_names) + 1)]
                    )
                    + "\n"
                    + req.prompt,
                    "width": width,
                    "height": height,
                    "length": length,
                    "ref_image_size": "match",
                },
            }
            for index, name in enumerate(reference_image_names):
                node_id = str(16 + index)
                nodes[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
                nodes["6"]["inputs"][f"ref_images.ref_image_{index}"] = [node_id, 0]
            for index, name in enumerate(reference_video_names):
                load_id = str(40 + index * 2)
                component_id = str(41 + index * 2)
                nodes[load_id] = {"class_type": "LoadVideo", "inputs": {"file": name}}
                nodes[component_id] = {"class_type": "GetVideoComponents", "inputs": {"video": [load_id, 0]}}
                nodes["6"]["inputs"][f"ref_videos.ref_video_{index}"] = [component_id, 0]
                nodes["6"]["inputs"][f"ref_video_audios.ref_video_audio_{index}"] = [component_id, 1]
        else:
            nodes["6"]["inputs"].update(
                {
                    "clip": ["3", 0],
                    "vae": ["4", 0],
                    "prompt": req.prompt,
                    "width": width,
                    "height": height,
                    "length": length,
                }
            )
        if opening_files is not None:
            if hold_names:
                for key, filename in zip(("hold_opening", "hold_resting"), hold_names, strict=True):
                    nodes[key] = {"class_type": "LoadImage", "inputs": {"image": filename}}
                output = ["hold_opening", 0]
                nodes["6"]["inputs"]["last_frame"] = ["hold_resting", 0]
                conditioning = ["6", 0]
                for position, fraction in enumerate((0.42, 0.78)):
                    key = f"hold_guide_{position}"
                    nodes[key] = {
                        "class_type": "MiniMaxH3AddGuide",
                        "inputs": {
                            "positive": conditioning,
                            "latent": ["6", 1],
                            "vae": ["4", 0],
                            "frame_idx": round((length - 1) * fraction),
                            "image": ["hold_resting", 0],
                        },
                    }
                    conditioning = [key, 0]
                nodes["7"]["inputs"]["conditioning"] = conditioning
            else:
                opening_nodes, output = qwen_opening_nodes(
                    req.opening_frame_prompt or req.prompt,
                    opening_files,
                    reference_image_names,
                    width,
                    height,
                    seed,
                    composition_goal=req.opening_composition_goal,
                    pose_goal=req.pose_goal,
                    plan_pose=plan_pose,
                    compose_activity_opening=req.compose_activity_opening,
                )
                nodes.update(opening_nodes)
            nodes["6"]["inputs"]["first_frame"] = output
            if req.fixed_ambient_scene and not (reference_image_names or first_frame_name or last_frame_name):
                nodes["6"]["inputs"]["last_frame"] = output
            continuity = (
                "镜头已经在拍摄首帧中的现场。从这一刻直接延续动作，整段保持首帧中的脸、穿戴结构、背景布局和光线；"
                "衣物和饰物随身体自然移动，环境随指定镜头运动连续显现。"
                if reference_image_names
                else "从首帧的现场接续所要求的变化，保持指定的机位、景别和物体与背景的空间关系。"
            )
            if reference_image_names and req.pose_goal:
                continuity = (
                    "沿用开场的现场、角色与拍摄距离，明确指定的运镜按原要求进行。"
                    + CHARACTER_FACE_MOTION
                    + "角色、衣饰和现场只随原定动作或机位变化。"
                )
            nodes["6"]["inputs"]["prompt"] = f"{continuity}\n{req.first_frame_motion or req.prompt}"
        if first_frame_name:
            nodes["16"] = {"class_type": "LoadImage", "inputs": {"image": first_frame_name}}
            nodes["6"]["inputs"]["first_frame"] = ["16", 0]
        if last_frame_name:
            nodes["17"] = {"class_type": "LoadImage", "inputs": {"image": last_frame_name}}
            nodes["6"]["inputs"]["last_frame"] = ["17", 0]
        if upscale:
            _add_upscale(nodes, width, height)
        return nodes

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        req = await _adaptive_request(req)
        self.validate_request(req)
        files, opening = await self._resolve_model_files(req)
        upscale = not req.face_stage and _UPSCALE_MODEL in await loader_options(
            self._client, "UpscaleModelLoader", "model_name"
        )
        hold_sources = None
        if (
            opening
            and req.observed_hold_images
            and req.reference_images
            and not (req.first_frame_image or req.last_frame_image)
            and await supports_nodes(self._client, "MiniMaxH3AddGuide")
        ):
            hold_sources = req.observed_hold_images
        plan_pose = bool(
            opening
            and req.reference_images
            and not hold_sources
            and (req.pose_goal or req.opening_composition_goal or req.face_guide_frames)
        ) and await supports_nodes(self._client, *POSE_PLANNING_NODES)
        uploaded: dict[str, str] = {}
        for reference in (req.first_frame_image, req.last_frame_image, *req.reference_images, *(hold_sources or ())):
            if reference and reference not in uploaded:
                uploaded[reference] = await self._upload_image(reference)
        first_frame_name = uploaded.get(req.first_frame_image or "")
        last_frame_name = uploaded.get(req.last_frame_image or "")
        reference_image_names = tuple(uploaded[reference] for reference in req.reference_images)
        reference_video_names: list[str] = []
        for reference in req.reference_videos:
            reference_video_names.append(await self._upload_video(reference))
        prompt_id = str(uuid4())
        try:
            response = await self._client.post(
                "/prompt",
                json={
                    "prompt": self._graph(
                        req,
                        files,
                        first_frame_name=first_frame_name,
                        last_frame_name=last_frame_name,
                        reference_image_names=reference_image_names,
                        reference_video_names=tuple(reference_video_names),
                        opening_files=opening,
                        hold_names=(uploaded[hold_sources[0]], uploaded[hold_sources[1]]) if hold_sources else None,
                        upscale=upscale,
                        plan_pose=plan_pose,
                    ),
                    "prompt_id": prompt_id,
                },
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise ProviderResultUnknownError("POST", self.config.base_url.rstrip("/") + "/prompt") from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"local video_gen submit failed: {response.status_code} {response.text[:400]}",
                status_code=response.status_code,
                body={"text": response.text[:500]},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResultUnknownError("POST", self.config.base_url.rstrip("/") + "/prompt") from exc
        task_id = body.get("prompt_id") if isinstance(body, dict) else None
        if not isinstance(task_id, str) or not task_id.strip():
            raise ProviderResultUnknownError("POST", self.config.base_url.rstrip("/") + "/prompt")
        return VideoJobStatus(task_id=task_id, status="queued")

    async def poll(self, task_id: str) -> VideoJobStatus:
        response = await self._client.get(f"/history/{task_id}")
        response.raise_for_status()
        payload = response.json()
        entry = payload.get(task_id) if isinstance(payload, dict) else None
        if not isinstance(entry, dict):
            return VideoJobStatus(task_id=task_id, status="queued")
        status = entry.get("status") or {}
        status_str = str(status.get("status_str") or "").lower()
        if status_str == "error":
            messages = status.get("messages") or []
            for event in reversed(messages) if isinstance(messages, list) else ():
                if not isinstance(event, (tuple, list)) or len(event) != 2:
                    continue
                kind, detail = event
                if kind == "execution_error" and isinstance(detail, dict) and detail.get("exception_message"):
                    return VideoJobStatus(
                        task_id=task_id, status="failed", error=str(detail["exception_message"])[:500]
                    )
            return VideoJobStatus(task_id=task_id, status="failed", error=str(messages)[:500])
        if not status.get("completed") and status_str not in {"success", "error"}:
            return VideoJobStatus(
                task_id=task_id,
                status="queued" if status_str in {"", "pending", "queued"} else "processing",
            )
        recorded = entry.get("prompt")
        graph = (
            recorded[2] if isinstance(recorded, list) and len(recorded) > 2 and isinstance(recorded[2], dict) else {}
        )
        saver = graph.get("15", {})
        guides = saver.get("class_type") == "SaveImage" and str(
            saver.get("inputs", {}).get("filename_prefix", "")
        ).startswith("video/spiritagent-guides-")
        suffix = ".png" if guides else ".mp4"
        outputs = entry.get("outputs") or {}
        delivered = (outputs.get("15", {}),) if guides else outputs.values()
        for output in delivered:
            for item in (output.get("images") or []) if isinstance(output, dict) else ():
                if isinstance(item, dict) and str(item.get("filename", "")).lower().endswith(suffix):
                    url = _view_url(self.config.base_url, item)
                    if url:
                        return VideoJobStatus(task_id=task_id, status="succeeded", download_url=url)
        return VideoJobStatus(
            task_id=task_id, status="failed", error=f"local video_gen completed without a {suffix} output"
        )

    async def cancel(self, task_id: str) -> Literal["cancelled", "finished", "not_cancellable", "unsupported"]:
        try:
            response = await self._client.post(f"/api/jobs/{task_id}/cancel", json={})
            if response.status_code not in (404, 405):
                if response.status_code >= 400:
                    response.raise_for_status()
                body = response.json()
                if isinstance(body, dict) and body.get("cancelled") is True:
                    return "cancelled"
                return "not_cancellable"
            response = await self._client.post("/queue", json={"delete": [task_id]})
            response.raise_for_status()
            body = response.json()
            if isinstance(body, dict) and task_id in (body.get("deleted") or []):
                return "cancelled"
            latest = await self.poll(task_id)
            return "finished" if latest.status in {"succeeded", "failed"} else "not_cancellable"
        except httpx.HTTPStatusError:
            latest = await self.poll(task_id)
            return "finished" if latest.status in {"succeeded", "failed"} else "not_cancellable"
