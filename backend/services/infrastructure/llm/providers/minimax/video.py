from typing import ClassVar

from ..base import ProviderConfig, VideoAsset, VideoGenProvider, VideoGenRequest, VideoJobStatus
from ..http import get_http
from ._errors import raise_for_minimax_response

# MiniMax 提供两套不兼容的视频 API；v1（Hailuo）由标准 token-plan 覆盖，v2（H3）需独立付费套餐，故 v1 保留为默认；按模型名前缀路由，未知名回落 v1（plan-covered 协议是安全侧）。
_V2_MODEL_PREFIX = "MiniMax-H3"


def _api_version(model: str) -> str:
    return "v2" if (model or "").startswith(_V2_MODEL_PREFIX) else "v1"


# v1（Hailuo）约束：离散时长、三档分辨率。
_V1_DURATIONS = (6, 10)
_V1_RESOLUTIONS = ("512P", "768P", "1080P")

# v2（H3）约束：区间内整数秒、两档分辨率。
_V2_DURATION_MIN, _V2_DURATION_MAX = 4, 15
_V2_RESOLUTIONS = ("768P", "2K")

# v1 任务状态枚举——大写，扁平响应体。
_V1_STATUS_MAP = {"Queueing": "queued", "Processing": "processing", "Success": "succeeded", "Fail": "failed"}

# MiniMax-H3 v2 task.status 枚举（文档：VideoTask.status）均为小写；把 "running" 并入内部 "processing" 但保留 "queued"，让调用方区分"未开始"与"进行中"；"cancelled" 归到 "failed"——后端生命周期无独立的 cancelled 状态，用户感知相同。
_STATUS_MAP = {
    "queued": "queued",
    "running": "processing",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "failed",
}

# 文档对 ContentItem.text 的限制；API 以 bad_request_error 拒收更长提示词，客户端提前失败以避免往返。
_MAX_PROMPT_CHARS = 7000


def _build_content(req: VideoGenRequest) -> list[dict]:
    """组装 MiniMax-H3 必需的多模态 content[]；text 必填且不超过 7000 字符；first_frame_image（i2v 模式）按 API 规约将 ratio 切换为 adaptive，H3 自图派生宽高比并忽略此处显式 ratio。"""
    if len(req.prompt) > _MAX_PROMPT_CHARS:
        raise ValueError(f"prompt exceeds MiniMax limit ({_MAX_PROMPT_CHARS} chars per ContentItem.text)")
    content: list[dict] = [{"type": "text", "text": req.prompt}]
    if req.first_frame_image:
        content.append({"type": "image_url", "image_url": {"url": req.first_frame_image}, "role": "first_frame"})
    return content


class MiniMaxVideoGenProvider(VideoGenProvider):
    """通过 MiniMax 提供视频生成，按模型名自动选择 v1（Hailuo，默认，duration ∈ {6,10}、resolution ∈ {512P,768P,1080P}，三阶段 submit/poll/fetch）或 v2（MiniMax-H3*，duration ∈ [4,15] 整数秒、resolution ∈ {768P,2K}，两阶段且 URL 内联）；默认 v1 因 H3 需独立付费订阅、否则开箱即失败；能力卡片设 model_name=MiniMax-H3 可启用 v2；版本相关参数校验放在此处，调用层无法预知模型故仅做并集预检、精确失败留在 submit。"""

    provider_name = "minimax"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"video_gen": "MiniMax-Hailuo-2.3"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"video_gen": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        model = req.model or self.config.model
        if _api_version(model) == "v2":
            path, payload = "/v2/video_generation", self._payload_v2(req, model)
        else:
            path, payload = "/v1/video_generation", self._payload_v1(req, model)

        resp = await self._client.post(path, json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=model)
        task_id = body.get("task_id", "")
        if not task_id:
            raise RuntimeError(f"MiniMax video_generation returned no task_id: {body}")
        return VideoJobStatus(task_id=task_id, status="queued", raw=body)

    @staticmethod
    def _payload_v1(req: VideoGenRequest, model: str) -> dict:
        if req.duration not in _V1_DURATIONS:
            raise ValueError(f"{model} (v1) requires duration in {_V1_DURATIONS}, got {req.duration!r}")
        if req.resolution not in _V1_RESOLUTIONS:
            raise ValueError(f"{model} (v1) requires resolution in {_V1_RESOLUTIONS}, got {req.resolution!r}")
        payload: dict = {"model": model, "prompt": req.prompt, "duration": req.duration, "resolution": req.resolution}
        if req.aspect_ratio:
            payload["aspect_ratio"] = req.aspect_ratio
        if req.first_frame_image:
            payload["first_frame_image"] = req.first_frame_image
        return payload

    @staticmethod
    def _payload_v2(req: VideoGenRequest, model: str) -> dict:
        if not isinstance(req.duration, int) or not _V2_DURATION_MIN <= req.duration <= _V2_DURATION_MAX:
            raise ValueError(
                f"{model} (v2) requires an integer duration in [{_V2_DURATION_MIN}, {_V2_DURATION_MAX}], got {req.duration!r}",
            )
        if req.resolution not in _V2_RESOLUTIONS:
            raise ValueError(f"{model} (v2) requires resolution in {_V2_RESOLUTIONS}, got {req.resolution!r}")
        payload: dict = {
            "model": model,
            "content": _build_content(req),
            "duration": req.duration,
            "resolution": req.resolution,
        }
        # t2v 必传 ratio 且不能是 adaptive；i2v 由 H3 从首帧派生 ratio 故不能传。
        if req.first_frame_image:
            payload["ratio"] = "adaptive"
        elif req.aspect_ratio:
            payload["ratio"] = req.aspect_ratio
        else:
            raise ValueError(f"{model} (v2) t2v mode requires aspect_ratio (one of 16:9, 9:16, 1:1, 4:3, 3:4, 21:9)")
        return payload

    async def poll(self, task_id: str) -> VideoJobStatus:
        # 任务行在 submit 时把 config.model 钉死（见 video_jobs._poll_and_finalize_locked），此处版本永远对应该 task_id 所属协议。
        if _api_version(self.config.model) == "v2":
            return await self._poll_v2(task_id)
        return await self._poll_v1(task_id)

    async def _poll_v1(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get("/v1/query/video_generation", params={"task_id": task_id})
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        raw_status = body.get("status", "Processing")
        norm = _V1_STATUS_MAP.get(raw_status, "processing")
        file_id = body.get("file_id") if norm == "succeeded" else None
        return VideoJobStatus(
            task_id=task_id,
            status=norm,
            file_id=file_id,
            download_url=None,  # v1 把 URL 隐藏在 fetch()/files.retrieve 后
            error=body.get("error_message") or body.get("error"),
            raw=body,
        )

    async def _poll_v2(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get(f"/v2/query/video_generation/{task_id}")
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        # 文档：GetVideoGenerationV2Resp = {task: VideoTask}（严格包装）；其他形态视为契约破坏，抛错让 worker 记 poll_failed 而非静默写半解析状态行。
        if not isinstance(body, dict) or not isinstance(body.get("task"), dict):
            raise RuntimeError(f"MiniMax poll returned unexpected body shape: {body!r}")
        task = body["task"]
        raw_status = str(task.get("status", "")).lower()
        norm = _STATUS_MAP.get(raw_status, "processing")
        content = task.get("content") or {}
        # video_generation / video_regeneration 暴露 content.url；H3-Context-IR 暴露 content.prompt（无 URL）——_download_and_store 仅在 succeeded 且 URL 存在时触发。
        download_url = content.get("url") if norm == "succeeded" else None
        # VideoTaskError = {code, message}（见文档）；非 dict 形态属契约漂移，写 repr 而非原值，避免作为用户消息直接暴露。
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
        if _api_version(self.config.model) == "v2":
            # H3 v2 下载 URL 由 poll 内联返回；fetch 不可达，仅为满足 ABC 保留。
            raise RuntimeError("MiniMax-H3 returns the download URL via poll(); fetch() is not used")
        resp = await self._client.get("/v1/files/retrieve", params={"file_id": file_id})
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        file_obj = body.get("file") or {}
        download_url = file_obj.get("download_url") or ""
        if not download_url:
            raise RuntimeError(f"MiniMax file retrieve returned no download_url: {body}")
        return VideoAsset(
            download_url=download_url,
            content_type=file_obj.get("content_type") or "video/mp4",
            size=file_obj.get("bytes"),
        )
