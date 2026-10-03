"""媒体质量链的无凭据快照、尝试游标和候选选择。"""

from dataclasses import replace
from typing import Literal
from uuid import uuid4

from components import SESSION_LOCAL, get_logger
from pydantic import BaseModel, ConfigDict, Field

from services.infrastructure.llm import (
    FailoverReason,
    ProviderConfig,
    classify_api_error,
    is_content_policy_error_message,
    resolve_provider_chain,
)

logger = get_logger(__name__)
MEDIA_IDENTITY_ACCEPT_SCORE = 75


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
    "timeout": "视频生成超过等待时限，供应商任务可能仍在进行；为避免重复计费，系统没有自动重试",
    "worker_failed": "视频生成服务异常，请稍后重试",
    "identity_changed": "角色外形已更新，旧参考生成的视频未交付",
    "quality_failed": "视频文件无法完成质量核查，请稍后重试",
    "authorization_revoked": "原通道授权已撤销，未继续制作或投递",
}


def video_provider_failure_reason(error: str | None) -> str:
    """已知视频失败终态只提取审核原因，不向用户暴露供应商原文。"""
    if error and (is_content_policy_error_message(error) or any(word in error for word in ("敏感", "违规"))):
        return "content_policy_blocked"
    return "provider_failed"


def video_failure_message(reason: str) -> str:
    """聊天与动作视频共用的失败文案；未知提交与审核拒绝保留各自恢复要求。"""
    if reason == "submit_result_unknown":
        reason = "result_unknown"
    return _VIDEO_FAILURE_COPY.get(reason, _VIDEO_FAILURE_COPY["provider_failed"])


class FrozenMediaProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    base_url: str
    model_overridden: bool = False
    max_images_per_request: int | None = Field(default=None, ge=1)
    background: Literal["transparent"] | None = None
    video_resolution: str | None = Field(default=None, min_length=1)

    @classmethod
    def from_config(cls, config: ProviderConfig) -> "FrozenMediaProvider":
        return cls(
            provider=config.provider_name,
            model=config.model,
            base_url=config.base_url,
            model_overridden=config.model_overridden,
        )


class MediaCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    attempt: int
    slot: int = 0
    score: int | None = Field(default=None, ge=0, le=100)
    evaluated: bool = False
    artifacts: list[str] = Field(default_factory=list)
    result_json: str | None = None


class MediaChainState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_id: str = Field(default_factory=lambda: uuid4().hex)
    providers: list[FrozenMediaProvider] = Field(default_factory=list)
    next_index: int = 0
    active_index: int | None = None
    phase: Literal["ready", "submitting", "processing", "storing", "evaluating", "complete"] = "ready"
    candidates: list[MediaCandidate] = Field(default_factory=list)
    stop_reason: str = ""
    result_url: str | None = None
    source_path: str | None = None

    def begin(self, index: int) -> None:
        self.active_index = index
        self.next_index = index + 1
        self.phase = "submitting"
        self.result_url = None
        self.source_path = None

    def best(self, slot: int = 0) -> MediaCandidate | None:
        candidates = [candidate for candidate in self.candidates if candidate.slot == slot]
        return max(candidates, key=lambda item: item.score if item.score is not None else -1, default=None)

    def accept_score(self, candidate: MediaCandidate, score: int | None) -> None:
        candidate.score = score
        candidate.evaluated = True
        if score is None:
            self.stop_reason = "score_unavailable"
        logger.info(
            "media identity candidate scored",
            extra={
                "provider": self.providers[candidate.attempt].provider,
                "model": self.providers[candidate.attempt].model,
                "chain_index": candidate.attempt,
                "slot": candidate.slot,
                "score": score,
            },
        )

    def needs_next(self, slot: int = 0) -> bool:
        best = self.best(slot)
        return (
            not self.stop_reason
            and self.next_index < len(self.providers)
            and (best is None or best.score is None or best.score < MEDIA_IDENTITY_ACCEPT_SCORE)
        )

    def finish(self, slots: int = 1) -> None:
        if not self.stop_reason:
            self.stop_reason = (
                "accepted"
                if all(
                    (best := self.best(slot)) is not None
                    and best.score is not None
                    and best.score >= MEDIA_IDENTITY_ACCEPT_SCORE
                    for slot in range(slots)
                )
                else "exhausted"
            )
        self.phase = "complete"


async def resolve_frozen_media_provider(
    user_id: int,
    service: str,
    frozen: FrozenMediaProvider,
) -> ProviderConfig | None:
    """凭据实时读取；任务模型与端点保持冻结，端点改变后不向新地址发送旧任务。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, service)
    current = next(
        (config for config in chain if config.provider_name == frozen.provider and config.base_url == frozen.base_url),
        None,
    )
    return (
        replace(current, model=frozen.model, model_overridden=frozen.model_overridden) if current is not None else None
    )


def media_failure_reason(exc: Exception) -> tuple[str, bool]:
    """返回失败原因及是否可安全尝试下一家；未知付费结果优先于其他错误。"""
    classified = classify_api_error(exc)
    if getattr(exc, "result_unknown", False) or classified.reason == FailoverReason.result_unknown:
        return "result_unknown", False
    if isinstance(exc, MediaProviderFailedError):
        return exc.reason, exc.reason != "result_unknown"
    return classified.reason.value, getattr(exc, "can_fallback", False) or classified.should_fallback
