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
    resolve_provider_chain,
)

logger = get_logger(__name__)
MEDIA_IDENTITY_ACCEPT_SCORE = 75


class MediaProviderFailedError(RuntimeError):
    """已获供应商失败终态，可安全推进到下一家。"""


class FrozenMediaProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    base_url: str
    model_overridden: bool = False
    max_images_per_request: int | None = Field(default=None, ge=1)

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
    result_file_id: str | None = None
    source_path: str | None = None

    def begin(self, index: int) -> None:
        self.active_index = index
        self.next_index = index + 1
        self.phase = "submitting"
        self.result_url = None
        self.result_file_id = None
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
    classified = getattr(exc, "classified", None) or classify_api_error(exc)
    if getattr(exc, "result_unknown", False) or classified.reason == FailoverReason.result_unknown:
        return "result_unknown", False
    return classified.reason.value, getattr(
        exc,
        "can_fallback",
        False,
    ) or classified.should_fallback or classified.reason in (
        FailoverReason.timeout,
        FailoverReason.overloaded,
    )
