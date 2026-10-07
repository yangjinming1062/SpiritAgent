"""回合内媒体产物、验图证据与生成预算；状态只能由执行层提供。"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal


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
    image_batch_claimed: bool = False
    video_claimed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def image_results(self) -> list[dict]:
        return [
            artifact.tool_view()
            for artifact in self.artifacts.values()
            if artifact.type == "image" and artifact.goal_id in self.current_versions
        ]

    def text_reply_media(self) -> list[dict[str, str]]:
        """文本回复每个交付目标只附加最新成功版本，重做失败时保留原图。"""
        selected: dict[str, dict[str, str]] = {}
        for artifact in self.artifacts.values():
            if artifact.goal_id in self.required_goals and artifact.status == "ready" and artifact.url:
                selected[artifact.goal_id] = {"type": artifact.type, "url": artifact.url}
        return list(selected.values())
