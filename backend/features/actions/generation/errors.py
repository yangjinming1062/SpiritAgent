"""动作制作：errors。"""

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.prompt_engineer import VisualReasoningError
from infrastructure.ai.providers.base import ProviderError
from infrastructure.video_processing.ffmpeg import VideoProcessError

from features.actions.generation.script import VideoScriptError
from features.media.chain import MediaProviderFailedError, media_failure_reason, video_failure_message
from features.media.image_generation import ImageGenerationError
from features.media.video_queue.contracts import VideoQueueStoppedError


class VideoPackError(RuntimeError):
    """动作包流程错误；str 为公开文案。"""


class VideoPackNotFoundError(VideoPackError):
    """目标动作包不存在或不属于调用者。"""


class VideoPackStateError(VideoPackError):
    """状态守卫拒绝（未就绪 / 状态冲突 / 参考版本过期）。"""


class _GenerationRowGoneError(Exception):
    """生成中的动作行已删除，或包已离开生成流程；只跳过受影响的目标。"""


def _generation_error(exc: Exception, *, media_type: str = "video") -> str:
    reason, _ = media_failure_reason(exc)
    if reason in {"result_unknown", "content_policy_blocked"} or isinstance(
        exc,
        (ProviderError, MediaProviderFailedError),
    ):
        if media_type == "image":
            return {
                "result_unknown": "图片提交结果尚未确认，请先核对已有结果",
                "content_policy_blocked": "图片生成被供应商拒绝，请调整姿态要求后重做",
                "quality_failed": "图片未通过检查，请重新生成",
                "quality_unavailable": "图片检查暂未完成，可以稍后继续制作",
            }.get(reason, "图片生成服务暂不可用，可重试已有任务或素材")
        return video_failure_message(reason)
    if isinstance(
        exc,
        (
            VideoProcessError,
            VideoPackError,
            VideoScriptError,
            ImageGenerationError,
            VisualReasoningError,
            LlmCallBlockedError,
            VideoQueueStoppedError,
        ),
    ):
        return str(exc)[:500]
    return "动作处理失败，可重试已有任务或素材"
