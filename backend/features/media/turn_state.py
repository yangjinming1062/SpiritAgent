"""回合内媒体产物与验图证据；状态只能由执行层提供。"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4


@dataclass(frozen=True)
class ImagePlan:
    request: str
    prompt: str
    size: str
    goal: str
    subject: Literal["self"] | None = None
    reference_image: str | None = None
    secondary_reference_image: str | None = None
    identity_json: str | None = None
    identity_reference_mode: Literal["primary", "portrait_and_outfit"] = "portrait_and_outfit"


@dataclass
class MediaArtifact:
    media_id: str
    type: Literal["image", "video"]
    goal_id: str
    status: Literal["pending", "ready", "failed", "result_unknown"]
    url: str | None = None
    error: str | None = None
    job_id: int | None = None
    identity_verdict: str | None = None
    bound_message_id: int | None = None
    media_revision: int = 0

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
            **({"identity_verdict": self.identity_verdict} if self.identity_verdict is not None else {}),
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
    task: str
    structured_reply: bool = False
    original_request: str = ""
    source_message_id: int | None = None
    request_scope: str = field(default_factory=lambda: uuid4().hex)
    asset_directory: str = ""
    artifacts: dict[str, MediaArtifact] = field(default_factory=dict)
    plans: dict[str, ImagePlan] = field(default_factory=dict)
    inspections: dict[str, MediaInspection] = field(default_factory=dict)
    current_versions: dict[str, str] = field(default_factory=dict)
    required_goals: set[str] = field(default_factory=set)
    regenerated_goals: set[str] = field(default_factory=set)
    image_requests: dict[str, list[str]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def has_unknown_image_result(self) -> bool:
        return any(
            self.artifacts[media_id].type == "image" and self.artifacts[media_id].status == "result_unknown"
            for media_id in self.current_versions.values()
        )

    def image_regeneration_blocker(
        self, media_id: str, inspection_id: str | None = None
    ) -> Literal["result_unknown", "not_ready", "inspection", "quota", "stale"] | None:
        if self.has_unknown_image_result():
            return "result_unknown"
        artifact = self.artifacts.get(media_id)
        if artifact is None or artifact.type != "image" or artifact.status != "ready" or not artifact.url:
            return "not_ready"
        inspection = self.inspections.get(media_id)
        if (
            inspection is None
            or inspection.verdict != "revise"
            or (inspection_id is not None and inspection.inspection_id != inspection_id)
        ):
            return "inspection"
        if artifact.goal_id in self.regenerated_goals:
            return "quota"
        if self.current_versions.get(artifact.goal_id) != media_id or artifact.goal_id not in self.plans:
            return "stale"
        return None

    def text_reply_media(self) -> list[dict[str, str]]:
        """文本回复每个交付目标只附加最新成功版本，重做失败时保留原图。"""
        selected: dict[str, dict[str, str]] = {}
        for artifact in self.artifacts.values():
            if artifact.goal_id in self.required_goals and artifact.status == "ready" and artifact.url:
                selected[artifact.goal_id] = {"type": artifact.type, "url": artifact.url}
        return list(selected.values())
