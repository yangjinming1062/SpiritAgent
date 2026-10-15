import base64
import binascii
import json
import math
from typing import ClassVar, Literal

from infrastructure.ai.providers.base import (
    ProviderConfig,
    ProviderError,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobState,
    VideoJobStatus,
    VideoReferenceMetadata,
)
from infrastructure.ai.providers.http import ProviderResultUnknownError, get_http
from infrastructure.ai.providers.minimax._errors import raise_for_minimax_response
from infrastructure.logger import get_logger

logger = get_logger(__name__)

_MODEL_CAPABILITIES: dict[str, tuple[tuple[int, ...], tuple[str, ...]]] = {
    "MiniMax-H3": (tuple(range(4, 16)), ("768P", "2K")),
    "MiniMax-H3-Max": (tuple(range(5, 16)), ("480P", "768P")),
}

# H3 v2 task.status 均为小写；running→processing 但保留 queued 区分「未开始」与「进行中」；cancelled→failed（后端生命周期无独立 cancelled）
_STATUS_MAP: dict[str, VideoJobState] = {
    "queued": "queued",
    "running": "processing",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "failed",
}

# 文档对 ContentItem.text 的限制；API 以 bad_request_error 拒收更长提示词，客户端提前失败
_MAX_PROMPT_CHARS = 7000
_MAX_REQUEST_BYTES = 64 * 1024 * 1024
_MAX_REFERENCE_VIDEO_BYTES = 50 * 1024 * 1024


def _validate_video_url(url: str) -> None:
    if not url.strip():
        raise ProviderError("MiniMax reference video URL must not be empty", status_code=400)
    if not url.startswith("data:"):
        return
    header, separator, encoded = url.partition(",")
    if not separator or not header.endswith(";base64"):
        raise ProviderError("MiniMax inline reference video must be a Base64 data URL", status_code=400)
    if len(encoded) > 4 * ((_MAX_REFERENCE_VIDEO_BYTES + 2) // 3):
        raise ProviderError("MiniMax reference video exceeds 50 MB", status_code=400)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProviderError("MiniMax reference video contains invalid Base64 data", status_code=400) from exc
    if not decoded or len(decoded) > _MAX_REFERENCE_VIDEO_BYTES:
        raise ProviderError("MiniMax reference video must contain data and be at most 50 MB", status_code=400)


def _build_content(req: VideoGenRequest) -> list[dict]:
    """组装文本与首尾帧；参考素材与首尾帧是互斥模式。"""
    if (req.reference_images or req.reference_videos) and (req.first_frame_image or req.last_frame_image):
        raise ProviderError("MiniMax reference media cannot be mixed with first/last frames", status_code=400)
    if len(req.reference_images) > 9:
        raise ProviderError("MiniMax accepts at most nine reference images", status_code=400)
    if len(req.reference_videos) > 3:
        raise ProviderError("MiniMax accepts at most three reference videos", status_code=400)
    for video in req.reference_videos:
        _validate_video_url(video)
    if not req.prompt.strip():
        raise ProviderError("MiniMax requires a non-empty prompt", status_code=400)
    if len(req.prompt) > _MAX_PROMPT_CHARS:
        raise ProviderError(
            f"prompt exceeds MiniMax limit ({_MAX_PROMPT_CHARS} chars per ContentItem.text)",
            status_code=400,
        )
    content: list[dict] = [{"type": "text", "text": req.prompt}]
    content.extend(
        {"type": "image_url", "image_url": {"url": image}, "role": "reference_image"} for image in req.reference_images
    )
    content.extend(
        {"type": "video_url", "video_url": {"url": video}, "role": "reference_video"} for video in req.reference_videos
    )
    if req.first_frame_image:
        content.append({"type": "image_url", "image_url": {"url": req.first_frame_image}, "role": "first_frame"})
    if req.last_frame_image:
        content.append({"type": "image_url", "image_url": {"url": req.last_frame_image}, "role": "last_frame"})
    return content


def _model_capabilities(model: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    try:
        return _MODEL_CAPABILITIES[model]
    except KeyError as exc:
        raise ProviderError(
            f"MiniMax video model must be one of {tuple(_MODEL_CAPABILITIES)}, got {model!r}",
            status_code=400,
        ) from exc


class MiniMaxVideoGenProvider(VideoGenProvider):
    """通过 MiniMax-H3 v2 异步接口提供视频生成。"""

    provider_name = "minimax"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.minimaxi.com"
    supports_first_frame = True
    supports_loop_frames = True
    supports_reference_images = True
    supports_reference_videos = True
    supports_adaptive_aspect_ratio = True
    aspect_ratios = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
    adaptive_aspect_ratio_range = (0.4, 2.5)
    first_frame_size_range = (256, 5760)

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.durations, self.resolutions = _MODEL_CAPABILITIES.get(config.model, ((), ()))
        self.supports_reference_videos = self.supports_reference_videos and config.model in _MODEL_CAPABILITIES
        self._client = get_http(config.base_url, config.api_key)

    def max_resolution(
        self,
        *,
        duration: int,
        first_frame: bool = False,
        last_frame: bool = False,
        reference_images: bool = False,
        reference_videos: bool = False,
    ) -> str | None:
        durations, resolutions = self.durations, self.resolutions
        if (
            duration not in durations
            or ((reference_images or reference_videos) and (first_frame or last_frame))
            or (reference_videos and not self.supports_reference_videos)
        ):
            return None
        return resolutions[-1]

    def validate_reference_video(self, metadata: VideoReferenceMetadata) -> None:
        if not self.supports_reference_videos:
            return super().validate_reference_video(metadata)
        if metadata.extension.lower().lstrip(".") not in {"mp4", "mov"}:
            raise ProviderError("MiniMax reference video must use an MP4 or MOV container", status_code=400)
        if metadata.video_codec.lower() not in {"h264", "hevc"}:
            raise ProviderError("MiniMax reference video must use H.264 or HEVC encoding", status_code=400)
        if any(codec.lower() not in {"aac", "mp3"} for codec in metadata.audio_codecs):
            raise ProviderError("MiniMax reference video audio must use AAC or MP3 encoding", status_code=400)
        if not 0 < metadata.size_bytes <= _MAX_REFERENCE_VIDEO_BYTES:
            raise ProviderError("MiniMax reference video must contain data and be at most 50 MB", status_code=400)
        if not math.isfinite(metadata.duration_seconds) or not 2 <= metadata.duration_seconds <= 15:
            raise ProviderError("MiniMax reference video duration must be between 2 and 15 seconds", status_code=400)
        for width, height in (
            (metadata.width, metadata.height),
            (metadata.display_width, metadata.display_height),
        ):
            if not (256 <= width <= 5760 and 256 <= height <= 5760):
                raise ProviderError(
                    "MiniMax reference video dimensions must be between 256 and 5760 px",
                    status_code=400,
                )
            if not 0.4 <= width / height <= 2.5:
                raise ProviderError("MiniMax reference video aspect ratio must be between 0.4 and 2.5", status_code=400)
        if not math.isfinite(metadata.fps) or not 23.976 <= metadata.fps <= 60:
            raise ProviderError("MiniMax reference video frame rate must be between 23.976 and 60 fps", status_code=400)

    def validate_request(self, req: VideoGenRequest) -> None:
        self._request_body(req)

    def _request_body(self, req: VideoGenRequest) -> bytes:
        super().validate_request(req)
        payload = json.dumps(
            self._payload(req, self.config.model),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(payload) > _MAX_REQUEST_BYTES:
            raise ProviderError(
                "MiniMax video request exceeds 64 MB; use public URLs for reference media",
                status_code=400,
            )
        return payload

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        payload = self._request_body(req)
        resp = await self._client.post(
            "/v2/video_generation",
            content=payload,
            headers={"Content-Type": "application/json"},
        )
        body = raise_for_minimax_response(resp)
        task_id = body.get("task_id", "")
        if not isinstance(task_id, str) or not task_id:
            raise ProviderResultUnknownError("POST", self.config.base_url)
        return VideoJobStatus(task_id=task_id, status="queued")

    @staticmethod
    def _payload(req: VideoGenRequest, model: str) -> dict:
        durations, resolutions = _model_capabilities(model)
        if not isinstance(req.duration, int) or req.duration not in durations:
            raise ProviderError(
                f"{model} requires an integer duration in [{durations[0]}, {durations[-1]}], got {req.duration!r}",
                status_code=400,
            )
        if req.resolution not in resolutions:
            raise ProviderError(
                f"{model} requires resolution in {resolutions}, got {req.resolution!r}",
                status_code=400,
            )
        payload: dict = {
            "model": model,
            "content": _build_content(req),
            "duration": req.duration,
            "resolution": req.resolution,
        }
        # 文生视频须指定具体比例；图生视频由输入帧决定比例。
        if (
            req.first_frame_image
            or req.last_frame_image
            or (req.reference_images or req.reference_videos)
            and req.aspect_ratio in (None, "adaptive")
        ):
            payload["ratio"] = "adaptive"
        elif req.aspect_ratio in ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"):
            payload["ratio"] = req.aspect_ratio
        else:
            raise ProviderError(
                f"{model} requires aspect_ratio (one of 16:9, 9:16, 1:1, 4:3, 3:4, 21:9)",
                status_code=400,
            )
        return payload

    async def poll(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get(f"/v2/query/video_generation/{task_id}")
        body = raise_for_minimax_response(resp)
        # 文档 GetVideoGenerationV2Resp = {task: VideoTask}（严格包装）；其他形态视为契约破坏，抛错由轮询方在时限内退避重试
        if not isinstance(body, dict) or not isinstance(body.get("task"), dict):
            raise RuntimeError(f"MiniMax poll returned unexpected body shape: {body!r}")
        task = body["task"]
        raw_status = str(task.get("status", "")).lower()
        norm = _STATUS_MAP.get(raw_status)
        if norm is None:
            # 未知状态继续轮询，原值写入日志便于运维排查。
            logger.warning(
                "unknown video task status",
                extra={"provider": "minimax", "task_id": task_id, "status": raw_status},
            )
            norm = "processing"
        content = task.get("content") or {}
        download_url = content.get("url") if norm == "succeeded" else None
        # VideoTaskError = {code, message}；非 dict 属契约漂移，写 repr 避免作为用户消息直接暴露
        err = task.get("error")
        if isinstance(err, dict):
            error_message = err.get("message") or err.get("code")
        elif err is None:
            error_message = None
        else:
            error_message = f"provider returned non-standard error: {err!r}"
        return VideoJobStatus(
            task_id=task_id,
            status=norm,
            download_url=download_url,
            error=error_message,
        )

    async def cancel(self, task_id: str) -> Literal["cancelled", "finished", "not_cancellable", "unsupported"]:
        resp = await self._client.delete(f"/v2/video_generation/{task_id}")
        if resp.status_code == 404:
            return "finished"
        if resp.status_code == 400:
            # 排队转为运行、已取消等竞争状态不能被记录为已成功撤销。
            latest = await self.poll(task_id)
            return "finished" if latest.status in {"succeeded", "failed"} else "not_cancellable"
        body = raise_for_minimax_response(resp)
        if body.get("action") == "cancelled" and body.get("status") == "cancelled":
            return "cancelled"
        if body.get("action") == "deleted" and body.get("status") == "deleted":
            return "finished"
        raise RuntimeError("MiniMax cancellation response did not confirm the operation")
