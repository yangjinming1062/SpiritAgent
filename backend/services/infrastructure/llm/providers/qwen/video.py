from typing import ClassVar

from ..base import ProviderConfig, VideoAsset, VideoGenProvider, VideoGenRequest, VideoJobStatus
from ..http import get_http
from ._errors import raise_for_qwen_response

# 工具层枚举（512P/768P/1080P/2K）与 wan3.0 原生档位（480P/720P/1080P）对齐；大小写不敏感。
_RESOLUTION_TO_API: dict[str, str] = {
    "512P": "480P",
    "480P": "480P",
    "768P": "720P",
    "720P": "720P",
    "1080P": "1080P",
    "2K": "1080P",
}
_STATUS_MAP = {
    "PENDING": "queued",
    "RUNNING": "processing",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "CANCELED": "failed",
}


class QwenVideoGenProvider(VideoGenProvider):
    """通过千问 wan3.0-video 异步 video-synthesis 提供视频生成。"""

    provider_name = "qwen"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"video_gen": "wan3.0-video"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"video_gen": 8_000}
    durations: tuple[int, ...] = tuple(range(2, 31))
    resolutions: tuple[str, ...] = ("480P", "720P", "1080P", "512P", "768P", "2K")
    supports_first_frame = True
    supports_loop_frames = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        if req.reference_images:
            raise ValueError("qwen video adapter does not support reference media combinations")
        if req.duration not in self.durations:
            raise ValueError(f"qwen video_gen requires duration in 2..30, got {req.duration!r}")
        resolution = (req.resolution or "").upper()
        api_resolution = _RESOLUTION_TO_API.get(resolution)
        if not api_resolution:
            raise ValueError(f"qwen video_gen requires resolution in {self.resolutions}, got {req.resolution!r}")

        media: list[dict] = []
        if req.first_frame_image:
            media.append({"type": "first_frame", "url": req.first_frame_image})
        if req.last_frame_image:
            media.append({"type": "last_frame", "url": req.last_frame_image})

        model = req.model or self.config.model
        payload = {
            "model": model,
            "input": {"prompt": req.prompt, **({"media": media} if media else {})},
            "parameters": {
                "resolution": api_resolution,
                "ratio": req.aspect_ratio or "adaptive",
                "duration": req.duration,
                "prompt_extend": True,
                "watermark": False,
            },
        }
        resp = await self._client.post(
            "/services/aigc/video-generation/video-synthesis",
            json=payload,
            headers={"X-DashScope-Async": "enable"},
        )
        body = raise_for_qwen_response(resp, family=self.provider_name, model=model)
        output = body.get("output") or {}
        task_id = output.get("task_id") or ""
        if not task_id:
            raise RuntimeError(f"qwen video_gen returned no task_id: {body}")
        return VideoJobStatus(task_id=task_id, status="queued", raw=body)

    async def poll(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get(f"/tasks/{task_id}")
        body = raise_for_qwen_response(resp, family=self.provider_name, model=self.config.model)
        output = body.get("output") or {}
        raw_status = str(output.get("task_status") or "").upper()
        norm = _STATUS_MAP.get(raw_status, "processing")
        download_url = output.get("video_url") if norm == "succeeded" else None
        return VideoJobStatus(
            task_id=task_id,
            status=norm,
            file_id=None,
            download_url=download_url,
            error=output.get("message") or (body.get("message") or None),
            raw=body,
        )

    async def fetch(self, file_id: str) -> VideoAsset:
        raise RuntimeError("qwen video_gen returns the download URL via poll(); fetch() is not used")
