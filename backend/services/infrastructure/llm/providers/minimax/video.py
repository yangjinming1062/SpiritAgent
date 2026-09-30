from typing import ClassVar

from ..base import ProviderConfig, VideoAsset, VideoGenProvider, VideoGenRequest, VideoJobState, VideoJobStatus
from ..http import get_http
from ._errors import raise_for_minimax_response

_DURATION_MIN, _DURATION_MAX = 4, 15
_RESOLUTIONS = ("768P", "2K")

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


def _build_content(req: VideoGenRequest) -> list[dict]:
    """组装文本与首帧；提示词上限按 ContentItem.text 校验。"""
    if len(req.prompt) > _MAX_PROMPT_CHARS:
        raise ValueError(f"prompt exceeds MiniMax limit ({_MAX_PROMPT_CHARS} chars per ContentItem.text)")
    content: list[dict] = [{"type": "text", "text": req.prompt}]
    if req.first_frame_image:
        content.append({"type": "image_url", "image_url": {"url": req.first_frame_image}, "role": "first_frame"})
    return content


class MiniMaxVideoGenProvider(VideoGenProvider):
    """通过 MiniMax-H3 v2 异步接口提供视频生成。"""

    provider_name = "minimax"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.minimaxi.com"
    DEFAULT_MODEL: ClassVar[str] = "MiniMax-H3"
    durations = tuple(range(_DURATION_MIN, _DURATION_MAX + 1))
    resolutions = _RESOLUTIONS
    supports_first_frame = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        if req.last_frame_image or req.reference_images:
            raise ValueError("MiniMax adapter does not implement pinned loop frames")
        model = req.model or self.config.model or self.DEFAULT_MODEL
        resp = await self._client.post("/v2/video_generation", json=self._payload(req, model))
        body = raise_for_minimax_response(resp)
        task_id = body.get("task_id", "")
        if not task_id:
            raise RuntimeError(f"MiniMax video_generation returned no task_id: {body}")
        return VideoJobStatus(task_id=task_id, status="queued", raw=body)

    @staticmethod
    def _payload(req: VideoGenRequest, model: str) -> dict:
        if not isinstance(req.duration, int) or not _DURATION_MIN <= req.duration <= _DURATION_MAX:
            raise ValueError(
                f"{model} requires an integer duration in [{_DURATION_MIN}, {_DURATION_MAX}], got {req.duration!r}",
            )
        if req.resolution not in _RESOLUTIONS:
            raise ValueError(f"{model} requires resolution in {_RESOLUTIONS}, got {req.resolution!r}")
        payload: dict = {
            "model": model,
            "content": _build_content(req),
            "duration": req.duration,
            "resolution": req.resolution,
        }
        # 文生视频须指定比例；图生视频由首帧决定比例。
        if req.first_frame_image:
            payload["ratio"] = "adaptive"
        elif req.aspect_ratio:
            payload["ratio"] = req.aspect_ratio
        else:
            raise ValueError(f"{model} t2v mode requires aspect_ratio (one of 16:9, 9:16, 1:1, 4:3, 3:4, 21:9)")
        return payload

    async def poll(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get(f"/v2/query/video_generation/{task_id}")
        body = raise_for_minimax_response(resp)
        # 文档 GetVideoGenerationV2Resp = {task: VideoTask}（严格包装）；其他形态视为契约破坏，抛错让 worker 记 poll_failed
        if not isinstance(body, dict) or not isinstance(body.get("task"), dict):
            raise RuntimeError(f"MiniMax poll returned unexpected body shape: {body!r}")
        task = body["task"]
        raw_status = str(task.get("status", "")).lower()
        norm = _STATUS_MAP.get(raw_status, "processing")
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
            file_id=None,
            download_url=download_url,
            error=error_message,
            raw=body,
        )

    async def fetch(self, file_id: str) -> VideoAsset:
        raise RuntimeError("MiniMax-H3 returns the download URL via poll(); fetch() is not used")
