from typing import ClassVar, Literal

from infrastructure.ai.providers.base import (
    ProviderConfig,
    ProviderError,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobState,
    VideoJobStatus,
)
from infrastructure.ai.providers.http import ProviderResultUnknownError, get_http
from infrastructure.ai.providers.qwen._errors import raise_for_qwen_response
from infrastructure.logger import get_logger

logger = get_logger(__name__)

# 工具层枚举（512P/768P/1080P/2K）与 wan3.0 原生档位（480P/720P/1080P）对齐；大小写不敏感。
_RESOLUTION_TO_API: dict[str, str] = {
    "512P": "480P",
    "480P": "480P",
    "768P": "720P",
    "720P": "720P",
    "1080P": "1080P",
    "2K": "1080P",
}
_DURATIONS = tuple(range(2, 31))
_STATUS_MAP: dict[str, VideoJobState] = {
    "PENDING": "queued",
    "RUNNING": "processing",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "CANCELED": "failed",
}


class QwenVideoGenProvider(VideoGenProvider):
    """通过千问 wan3.0-video 异步 video-synthesis 提供视频生成。"""

    provider_name = "qwen"
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/api/v1"
    durations = _DURATIONS
    resolutions = ("480P", "720P", "1080P", "512P", "768P", "2K")
    supports_first_frame = True
    supports_loop_frames = True
    supports_reference_images = True
    supports_adaptive_aspect_ratio = True
    aspect_ratios = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
    adaptive_aspect_ratio_range = (1 / 8, 8.0)
    first_frame_size_range = (240, 8000)

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    def native_resolution(self, resolution: str) -> str | None:
        return _RESOLUTION_TO_API.get(resolution.upper())

    def available_resolutions(
        self,
        *,
        duration: int,
        first_frame: bool = False,
        last_frame: bool = False,
        reference_images: bool = False,
        reference_videos: bool = False,
    ) -> tuple[str, ...]:
        if (
            self._reject_reference_media(
                first_frame=first_frame,
                last_frame=last_frame,
                reference_images=reference_images,
                reference_videos=reference_videos,
            )
            or duration not in _DURATIONS
        ):
            return ()
        return "480P", "720P", "1080P"

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        self.validate_request(req)
        if req.reference_images and len(req.reference_images) > 10:
            raise ProviderError("qwen accepts at most ten reference images", status_code=400)
        if req.duration not in _DURATIONS:
            raise ProviderError(f"qwen video_gen requires duration in 2..30, got {req.duration!r}", status_code=400)
        api_resolution = self.native_resolution(req.resolution or "")
        if not api_resolution:
            raise ProviderError(
                f"qwen video_gen requires resolution in {self.resolutions}, got {req.resolution!r}",
                status_code=400,
            )

        media: list[dict] = [{"type": "reference_image", "url": image} for image in req.reference_images]
        if req.first_frame_image:
            media.append({"type": "first_frame", "url": req.first_frame_image})
        if req.last_frame_image:
            media.append({"type": "last_frame", "url": req.last_frame_image})

        model = self.config.model
        payload = {
            "model": model,
            "input": {"prompt": req.prompt, **({"media": media} if media else {})},
            "parameters": {
                "resolution": api_resolution,
                "ratio": req.aspect_ratio or "adaptive",
                "duration": req.duration,
                "prompt_extend": False,
                "watermark": False,
            },
        }
        resp = await self._client.post(
            "/services/aigc/video-generation/video-synthesis",
            json=payload,
            headers={"X-DashScope-Async": "enable"},
        )
        body = raise_for_qwen_response(resp)
        output = body.get("output")
        if not isinstance(output, dict):
            raise ProviderResultUnknownError("POST", self.config.base_url)
        task_id = output.get("task_id") or ""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ProviderResultUnknownError("POST", self.config.base_url)
        return VideoJobStatus(task_id=task_id, status="queued")

    async def poll(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get(f"/tasks/{task_id}")
        body = raise_for_qwen_response(resp)
        output = body.get("output") or {}
        raw_status = str(output.get("task_status") or "").upper()
        norm = _STATUS_MAP.get(raw_status)
        if norm is None:
            # 未知状态继续轮询，原值写入日志便于运维排查。
            logger.warning(
                "unknown video task status",
                extra={"provider": "qwen", "task_id": task_id, "status": raw_status},
            )
            norm = "processing"
        download_url = output.get("video_url") if norm == "succeeded" else None
        return VideoJobStatus(
            task_id=task_id,
            status=norm,
            download_url=download_url,
            error=output.get("message") or (body.get("message") or None),
        )

    async def cancel(self, task_id: str) -> Literal["cancelled", "finished", "not_cancellable", "unsupported"]:
        resp = await self._client.post(f"/tasks/{task_id}/cancel")
        if resp.status_code == 404:
            return "finished"
        if resp.status_code == 400 and resp.json().get("code") == "UnsupportedOperation":
            latest = await self.poll(task_id)
            return "finished" if latest.status in {"succeeded", "failed"} else "not_cancellable"
        raise_for_qwen_response(resp)
        return "cancelled"
