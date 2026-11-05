"""媒体质量链的无凭据快照、尝试游标和候选选择。"""

from dataclasses import replace
from typing import Literal
from uuid import UUID, uuid4

from infrastructure.ai.error_classifier import FailoverReason, classify_api_error, is_content_policy_error_message
from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.providers.base import ProviderConfig, ServiceType
from infrastructure.ai.selection import GenerationOrigin
from infrastructure.ai.tasks import AI_TASK_BY_ID, AiTaskKey
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from pydantic import BaseModel, ConfigDict, Field

from features.media.review_contracts import ReviewEvidence, VisualReview

logger = get_logger(__name__)


class MediaProviderFailedError(RuntimeError):
    """已获供应商失败终态，可安全推进到下一家。"""

    def __init__(self, message: str, *, reason: str = "provider_failed") -> None:
        super().__init__(message)
        self.reason = reason


_VIDEO_FAILURE_COPY: dict[str, str] = {
    "result_unknown": "视频提交结果不确定，供应商可能已接单；为避免重复计费，系统没有自动重试",
    "submit_failed": "视频提交失败，请稍后重试",
    "provider_unavailable": "视频生成服务配置已变更，请稍后重试",
    "provider_failed": "视频生成失败，请稍后重试",
    "content_policy_blocked": "内容审核未通过，请调整请求内容或参考形象后重试",
    "download_failed": "视频下载失败，请稍后重试",
    "worker_failed": "视频生成服务异常，请稍后重试",
    "identity_changed": "角色外形已更新，旧参考生成的视频未交付",
    "quality_failed": "视频未通过检查，请重新生成",
    "quality_unavailable": "视频检查暂未完成，可以稍后继续制作",
    "rate_limit": "视频服务繁忙，正在等待重试",
    "queue_stopped": "本次视频制作已结束，请按需重新发起",
}


def video_provider_failure_reason(error: str | None) -> str:
    """已知视频失败终态只提取审核原因，不向用户暴露供应商原文。"""
    if error and (is_content_policy_error_message(error) or any(word in error for word in ("敏感", "违规"))):
        return "content_policy_blocked"
    return "provider_failed"


def video_failure_message(reason: str) -> str:
    """聊天与影像视频共用的失败文案；未知提交与审核拒绝保留各自恢复要求。"""
    return _VIDEO_FAILURE_COPY.get(reason, _VIDEO_FAILURE_COPY["provider_failed"])


class FrozenMediaProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    base_url: str
    task: AiTaskKey
    provider_id: UUID
    model_card_id: UUID
    max_images_per_request: int | None = Field(default=None, ge=1)
    background: Literal["transparent"] | None = None
    image_size: str | None = None
    image_aspect_ratio: str | None = None
    image_resolution: str | None = None
    image_exact_size: bool = False
    video_resolution: str | None = Field(default=None, min_length=1)
    video_aspect_ratio: str | None = None

    def generation_origin(self, service_type: ServiceType) -> GenerationOrigin:
        return GenerationOrigin(
            task=self.task,
            service_type=service_type,
            provider_id=self.provider_id,
            model_card_id=self.model_card_id,
            model=self.model,
        )

    @classmethod
    def from_config(cls, config: ProviderConfig) -> "FrozenMediaProvider":
        if config.task is None or config.service_type.value not in AI_TASK_BY_ID[config.task].services:
            raise ValueError("media task requires an AI task")
        return cls(
            provider=config.provider_name,
            model=config.model,
            base_url=config.base_url,
            task=config.task,
            provider_id=config.provider_id,
            model_card_id=config.model_card_id,
        )


class MediaCandidate(BaseModel):
    """候选媒体与唯一检查结论；人工排除独立于模型判断，采纳由两者共同推出。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    generation_id: str | None = None
    attempt: int
    slot: int = 0
    evidence: ReviewEvidence | None = None
    review: VisualReview | None = None
    evaluated: bool = False
    excluded: bool = False
    artifacts: list[str] = Field(default_factory=list)
    result_json: str | None = None

    @property
    def accepted(self) -> bool | None:
        if self.excluded:
            return False
        if not self.evaluated:
            return None
        return self.review is None or self.review.verdict == "pass"


class MediaChainState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1

    generation_id: str = Field(default_factory=lambda: uuid4().hex)
    providers: list[FrozenMediaProvider] = Field(default_factory=list)
    next_index: int = 0
    active_index: int | None = None
    phase: Literal["ready", "submitting", "processing", "storing", "evaluating", "complete"] = "ready"
    candidates: list[MediaCandidate] = Field(default_factory=list)
    stop_reason: str = ""
    result_url: str | None = None
    source_path: str | None = None
    queue_request_id: str | None = None

    def current_attempt_index(self) -> int:
        return (
            self.active_index if self.active_index is not None else max((c.attempt for c in self.candidates), default=0)
        )

    def unknown_submission(self) -> bool:
        """远端提交可能已被受理但结果未确认；此状态下禁止自动重发。"""
        return self.stop_reason == "result_unknown" or (self.phase == "submitting" and not self.queue_request_id)

    def begin(self, index: int) -> None:
        self.active_index = index
        self.next_index = index + 1
        self.phase = "submitting"
        self.result_url = None
        self.source_path = None
        self.queue_request_id = None

    def best(self, slot: int = 0) -> MediaCandidate | None:
        return self.best_qualified(slot) or next((c for c in self.candidates if c.slot == slot), None)

    def best_qualified(self, slot: int = 0) -> MediaCandidate | None:
        return next(
            (
                c
                for c in self.candidates
                if c.slot == slot
                and c.evaluated
                and c.accepted is not False
                and c.review is not None
                and c.review.verdict == "pass"
            ),
            None,
        )

    def needs_next(self, slot: int = 0) -> bool:
        return not self.stop_reason and self.next_index < len(self.providers) and self.best_qualified(slot) is None

    def finish(self, slots: int = 1) -> None:
        if not self.stop_reason:
            self.stop_reason = (
                "accepted" if all(self.best_qualified(slot) is not None for slot in range(slots)) else "exhausted"
            )
        self.phase = "complete"


async def resolve_frozen_media_provider(
    user_id: int, service: str, frozen: FrozenMediaProvider
) -> ProviderConfig | None:
    """凭据实时读取；任务模型与端点保持冻结，端点改变后不向新地址发送旧任务。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, service, task=frozen.task)
    current = next(
        (
            config
            for config in chain
            if config.model_card_id == frozen.model_card_id
            and config.provider_id == frozen.provider_id
            and config.provider_name == frozen.provider
            and config.base_url == frozen.base_url
        ),
        None,
    )
    return replace(current, model=frozen.model) if current is not None else None


def media_failure_reason(exc: Exception) -> tuple[str, bool]:
    """返回失败原因及是否可安全尝试下一家；未知付费结果优先于其他错误。"""
    classified = classify_api_error(exc)
    if getattr(exc, "result_unknown", False) or classified.reason == FailoverReason.result_unknown:
        return "result_unknown", False
    if getattr(exc, "can_fallback", None) is False:
        return getattr(exc, "reason", classified.reason.value), False
    if isinstance(exc, MediaProviderFailedError):
        return exc.reason, exc.reason != "result_unknown"
    return classified.reason.value, getattr(exc, "can_fallback", False) or classified.should_fallback
