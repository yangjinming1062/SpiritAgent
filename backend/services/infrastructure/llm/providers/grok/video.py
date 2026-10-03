from typing import ClassVar

from components import get_logger

from ..base import ProviderConfig, ProviderError, VideoGenProvider, VideoGenRequest, VideoJobState, VideoJobStatus
from ..http import ProviderResultUnknownError, get_http
from ._errors import raise_for_grok_response

logger = get_logger(__name__)

# xAI 生命周期：queued / processing / done / failed / expired；expired 与 failed 均为终态失败（worker 可停止轮询），统一映射为内部 "failed"，避免 worker 多分支。
_STATUS_MAP: dict[str, VideoJobState] = {
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
_SUPPORTED_RESOLUTIONS = ("480p", "720p", "1080p")


class GrokVideoGenProvider(VideoGenProvider):
    """通过 xAI submit→POST /videos/generations 取得 request_id；poll→GET /videos/{request_id} 读取状态与成品 URL。"""

    provider_name = "grok"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.x.ai/v1"
    DEFAULT_MODEL: ClassVar[str] = "grok-imagine-video-1.5"

    # 能力声明（与 submit 校验一致）；分辨率按成本升序，键取规范小写。
    durations = _SUPPORTED_DURATIONS
    resolutions = ("480p", "720p", "1080p")
    supports_first_frame = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)
        self.supports_loop_frames = (config.model or self.DEFAULT_MODEL) == "grok-imagine-video-1.5"
        self.supports_reference_images = self.supports_loop_frames

    def max_resolution(
        self,
        *,
        duration: int,
        first_frame: bool = False,
        last_frame: bool = False,
        reference_images: bool = False,
    ) -> str | None:
        if duration not in _SUPPORTED_DURATIONS:
            return None
        if last_frame or reference_images:
            return "720p" if self.supports_loop_frames else None
        return "1080p" if (self.config.model or self.DEFAULT_MODEL) == "grok-imagine-video-1.5" else "720p"

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        model = self.config.model

        if len(req.prompt) > _MAX_PROMPT_CHARS:
            raise ProviderError(f"prompt exceeds xAI limit ({_MAX_PROMPT_CHARS} chars)", status_code=400)
        if req.duration not in _SUPPORTED_DURATIONS:
            raise ProviderError(
                f"{model} requires duration in {_SUPPORTED_DURATIONS}, got {req.duration!r}",
                status_code=400,
            )
        resolution = req.resolution.lower()
        if resolution not in _SUPPORTED_RESOLUTIONS:
            raise ProviderError(
                f"{model} requires resolution in {_SUPPORTED_RESOLUTIONS}, got {req.resolution!r}",
                status_code=400,
            )

        payload: dict = {"model": model, "prompt": req.prompt, "duration": req.duration, "resolution": resolution}
        if req.aspect_ratio:
            payload["aspect_ratio"] = req.aspect_ratio
        if req.first_frame_image:
            payload["image"] = {"url": req.first_frame_image, "type": "image_url"}

        if req.last_frame_image or req.reference_images:
            if model != "grok-imagine-video-1.5" or req.resolution.lower() not in ("480p", "720p"):
                raise ProviderError(
                    "Grok reference/first-last frame mode requires grok-imagine-video-1.5 at up to 720p",
                    status_code=400,
                )
            if len(req.reference_images) > 7:
                raise ProviderError("Grok accepts at most seven reference images", status_code=400)
            if req.last_frame_image:
                payload["last_frame"] = {"url": req.last_frame_image}
            if req.reference_images:
                payload["reference_images"] = [{"url": image} for image in req.reference_images]

        resp = await self._client.post("/videos/generations", json=payload)
        body = raise_for_grok_response(resp)

        request_id = body.get("request_id", "")
        if not isinstance(request_id, str) or not request_id:
            raise ProviderResultUnknownError("POST", self.config.base_url)
        return VideoJobStatus(task_id=request_id, status="queued")

    async def poll(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get(f"/videos/{task_id}")
        body = raise_for_grok_response(resp)

        raw_status = str(body.get("status", "")).lower()
        norm = _STATUS_MAP.get(raw_status)
        if norm is None:
            # 未知状态继续轮询，原值写入日志便于运维排查。
            logger.warning(
                "unknown video task status",
                extra={"provider": "grok", "task_id": task_id, "status": raw_status},
            )
            return VideoJobStatus(task_id=task_id, status="processing")

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

        return VideoJobStatus(task_id=task_id, status=norm, download_url=download_url, error=error)
