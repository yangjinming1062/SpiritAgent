from typing import ClassVar

from ..base import ProviderConfig, VideoAsset, VideoGenProvider, VideoGenRequest, VideoJobStatus
from ..http import get_http
from ._errors import raise_for_grok_response

# xAI 生命周期：queued / processing / done / failed / expired；expired 与 failed 均为终态失败（worker 可停止轮询），统一映射为内部 "failed"，避免 worker 多分支。
_STATUS_MAP = {
    "queued": "queued",
    "processing": "processing",
    "pending": "processing",
    "running": "processing",
    "done": "succeeded",
    "failed": "failed",
    "expired": "failed",
}

# 文档限制 video generation 提示词 ≤7000 字符；客户端提前拒绝，避免无谓往返。
_MAX_PROMPT_CHARS = 7000

# xAI 文档（Imagine Overview 与 grok-imagine-video-1.5 模型页）显示时长范围 1–15s；接受全范围而非窄枚举，确保 VideoGenRequest 默认 duration=6 不会被客户端拒。
_SUPPORTED_DURATIONS = tuple(range(1, 16))  # 1..15 inclusive
# 文档规定分辨率为小写（如 "720p"、"1080p"），同时接受大写以屏蔽大小写差异。
_SUPPORTED_RESOLUTIONS = ("480p", "720p", "1080p", "480P", "720P", "1080P")


class GrokVideoGenProvider(VideoGenProvider):
    """通过 xAI 的两阶段异步管道提供视频生成：submit→POST /videos/generations 返回 request_id；poll→GET /videos/{request_id} 返回状态与下载 URL（done 时 URL 内联）；fetch 不可达（URL 仅由 poll 返回）；默认模型 grok-imagine-video-1.5。"""

    provider_name = "grok"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"video_gen": "grok-imagine-video-1.5"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"video_gen": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        model = req.model or self.config.model

        if len(req.prompt) > _MAX_PROMPT_CHARS:
            raise ValueError(f"prompt exceeds xAI limit ({_MAX_PROMPT_CHARS} chars)")
        if req.duration not in _SUPPORTED_DURATIONS:
            raise ValueError(f"{model} requires duration in {_SUPPORTED_DURATIONS}, got {req.duration!r}")
        if req.resolution not in _SUPPORTED_RESOLUTIONS:
            raise ValueError(f"{model} requires resolution in {_SUPPORTED_RESOLUTIONS}, got {req.resolution!r}")

        payload: dict = {"model": model, "prompt": req.prompt, "duration": req.duration, "resolution": req.resolution}
        if req.aspect_ratio:
            payload["aspect_ratio"] = req.aspect_ratio
        if req.first_frame_image:
            payload["image"] = {"url": req.first_frame_image, "type": "image_url"}

        resp = await self._client.post("/videos/generations", json=payload)
        body = raise_for_grok_response(resp, provider=self.provider_name, model=model)

        request_id = body.get("request_id", "")
        if not request_id:
            raise RuntimeError(f"grok video_generation returned no request_id: {body}")
        return VideoJobStatus(task_id=request_id, status="queued", raw=body)

    async def poll(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get(f"/videos/{task_id}")
        body = raise_for_grok_response(resp, provider=self.provider_name, model=self.config.model)

        raw_status = str(body.get("status", "")).lower()
        norm = _STATUS_MAP.get(raw_status)
        if norm is None:
            # 未知状态继续轮询，但把原值写入日志便于运维排查。
            return VideoJobStatus(
                task_id=task_id,
                status="processing",
                error=f"unknown grok video status: {raw_status!r}",
                raw=body,
            )

        video = body.get("video") or {}
        download_url = video.get("url") if norm == "succeeded" else None
        # error 形态不定：{"code","message"} dict 或自由字符串，统一规整为可读消息。
        error_raw = body.get("error") if norm == "failed" else None
        if isinstance(error_raw, dict):
            error = error_raw.get("message") or error_raw.get("code") or str(error_raw)
        elif isinstance(error_raw, str):
            error = error_raw
        elif error_raw is None:
            error = None
        else:
            error = f"provider returned non-standard error: {error_raw!r}"

        return VideoJobStatus(task_id=task_id, status=norm, download_url=download_url, error=error, raw=body)

    async def fetch(self, file_id: str) -> VideoAsset:
        # xAI 下载 URL 由 poll 内联返回；fetch 不可达，仅为满足 ABC 保留。
        raise RuntimeError("grok video returns the download URL via poll(); fetch() is not used")
