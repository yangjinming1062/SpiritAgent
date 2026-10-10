"""回合内媒体产物、验图证据与生成预算；状态只能由执行层提供。"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal

# 媒体请求受理后保留的工具步：足够核对结果或补做一次，其余回合时间留给最终回复。
MEDIA_FOLLOW_UP_STEPS = 2


@dataclass(frozen=True)
class ImagePlan:
    request: str
    prompt: str
    size: str
    reference_image: str | None = None
    secondary_reference_image: str | None = None
    identity_json: str | None = None


@dataclass
class MediaArtifact:
    media_id: str
    type: Literal["image", "video"]
    goal_id: str
    status: Literal["pending", "ready", "failed", "result_unknown"]
    url: str | None = None
    error: str | None = None
    job_id: int | None = None
    identity_score: int | None = None
    bound_message_id: int | None = None

    def tool_view(self) -> dict:
        return {
            "media_id": self.media_id,
            "type": self.type,
            "status": self.status,
            "goal_id": self.goal_id,
            "already_delivered": self.bound_message_id is not None,
            **({"url": self.url} if self.url else {}),
            **({"error": self.error} if self.error else {}),
            **({"task_id": str(self.job_id)} if self.job_id is not None else {}),
            **({"identity_score": self.identity_score} if self.identity_score is not None else {}),
        }


@dataclass(frozen=True)
class MediaInspection:
    inspection_id: str
    media_id: str
    verdict: Literal["pass", "revise", "unavailable"]
    issues: tuple[str, ...]


@dataclass
class MediaTurnState:
    user_id: int
    session_id: str
    structured_reply: bool = False
    original_request: str = ""
    source_message_id: int | None = None
    asset_directory: str = ""
    artifacts: dict[str, MediaArtifact] = field(default_factory=dict)
    plans: dict[str, ImagePlan] = field(default_factory=dict)
    inspections: dict[str, MediaInspection] = field(default_factory=dict)
    current_versions: dict[str, str] = field(default_factory=dict)
    required_goals: set[str] = field(default_factory=set)
    regenerated_goals: set[str] = field(default_factory=set)
    # 本回合已受理的图片请求（规范化请求 → 其 media_id）、已用的图片预算，以及提交图片请求所在的工具步序号。
    image_requests: dict[str, list[str]] = field(default_factory=dict)
    image_budget_used: int = 0
    tool_round: int = 0
    image_round: int | None = None
    media_round: int | None = None
    video_claimed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def mark_media_accepted(self) -> None:
        """记录本回合首个媒体请求受理时所在的工具步。"""
        if self.media_round is None:
            self.media_round = self.tool_round

    def final_reply_due(self) -> bool:
        """媒体请求受理后只再允许有限的工具步；用尽后模型调用只生成最终回复，不再披露工具。"""
        return self.media_round is not None and self.tool_round - self.media_round >= MEDIA_FOLLOW_UP_STEPS

    def text_reply_media(self) -> list[dict[str, str]]:
        """文本回复每个交付目标只附加最新成功版本，重做失败时保留原图。"""
        selected: dict[str, dict[str, str]] = {}
        for artifact in self.artifacts.values():
            if artifact.goal_id in self.required_goals and artifact.status == "ready" and artifact.url:
                selected[artifact.goal_id] = {"type": artifact.type, "url": artifact.url}
        return list(selected.values())
