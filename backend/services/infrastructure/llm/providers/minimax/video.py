from typing import ClassVar

from components import get_logger

from ..base import ProviderConfig, VideoGenProvider, VideoGenRequest, VideoJobState, VideoJobStatus
from ..http import get_http
from ._errors import raise_for_minimax_response

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


def _build_content(req: VideoGenRequest) -> list[dict]:
    """组装文本与首尾帧；参考素材与首尾帧是互斥模式。"""
    if req.reference_images:
        raise ValueError("MiniMax adapter does not implement reference media mode; it cannot be mixed with frames")
    if not req.prompt.strip():
        raise ValueError("MiniMax requires a non-empty prompt")
    if len(req.prompt) > _MAX_PROMPT_CHARS:
        raise ValueError(f"prompt exceeds MiniMax limit ({_MAX_PROMPT_CHARS} chars per ContentItem.text)")
    content: list[dict] = [{"type": "text", "text": req.prompt}]
    if req.first_frame_image:
        content.append({"type": "image_url", "image_url": {"url": req.first_frame_image}, "role": "first_frame"})
    if req.last_frame_image:
        content.append({"type": "image_url", "image_url": {"url": req.last_frame_image}, "role": "last_frame"})
    return content


def _model_capabilities(model: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    try:
        return _MODEL_CAPABILITIES[model]
    except KeyError as exc:
        raise ValueError(f"MiniMax video model must be one of {tuple(_MODEL_CAPABILITIES)}, got {model!r}") from exc


class MiniMaxVideoGenProvider(VideoGenProvider):
    """通过 MiniMax-H3 v2 异步接口提供视频生成。"""

    provider_name = "minimax"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.minimaxi.com"
    DEFAULT_MODEL: ClassVar[str] = "MiniMax-H3"
    supports_first_frame = True
    supports_loop_frames = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.durations, self.resolutions = _model_capabilities(config.model or self.DEFAULT_MODEL)
        self._client = get_http(config.base_url, config.api_key)

    def max_resolution(
        self,
        *,
        duration: int,
        first_frame: bool = False,
        last_frame: bool = False,
        reference_images: bool = False,
    ) -> str | None:
        durations, resolutions = _model_capabilities(self.config.model or self.DEFAULT_MODEL)
        if duration not in durations or reference_images:
            return None
        return resolutions[-1]

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        model = self.config.model or self.DEFAULT_MODEL
        resp = await self._client.post("/v2/video_generation", json=self._payload(req, model))
        body = raise_for_minimax_response(resp)
        task_id = body.get("task_id", "")
        if not task_id:
            raise RuntimeError(f"MiniMax video_generation returned no task_id: {body}")
        return VideoJobStatus(task_id=task_id, status="queued")

    @staticmethod
    def _payload(req: VideoGenRequest, model: str) -> dict:
        durations, resolutions = _model_capabilities(model)
        if not isinstance(req.duration, int) or req.duration not in durations:
            raise ValueError(
                f"{model} requires an integer duration in [{durations[0]}, {durations[-1]}], got {req.duration!r}",
            )
        if req.resolution not in resolutions:
            raise ValueError(f"{model} requires resolution in {resolutions}, got {req.resolution!r}")
        payload: dict = {
            "model": model,
            "content": _build_content(req),
            "duration": req.duration,
            "resolution": req.resolution,
        }
        # 文生视频须指定具体比例；图生视频由输入帧决定比例。
        if req.first_frame_image or req.last_frame_image:
            payload["ratio"] = "adaptive"
        elif req.aspect_ratio in ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"):
            payload["ratio"] = req.aspect_ratio
        else:
            raise ValueError(f"{model} t2v mode requires aspect_ratio (one of 16:9, 9:16, 1:1, 4:3, 3:4, 21:9)")
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
