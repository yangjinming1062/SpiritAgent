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
    pose_goal: str | None = None
    identity_json: str | None = None
    identity_reference_mode: Literal["primary", "portrait_and_outfit"] = "portrait_and_outfit"
    performance_context: str = ""


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
    parent_media_id: str | None = None
    image_plan: ImagePlan | None = None

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
    verdict: Literal["pass", "revise", "stalled", "unavailable"]
    issues: tuple[str, ...]
    parent_media_id: str | None


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
    inspections: dict[str, MediaInspection] = field(default_factory=dict)
    current_versions: dict[str, str] = field(default_factory=dict)
    required_goals: set[str] = field(default_factory=set)
    image_requests: dict[str, list[str]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def image_regeneration_blocker(
        self, media_id: str, inspection_id: str | None = None
    ) -> Literal["not_ready", "inspection", "stale"] | None:
        artifact = self.artifacts.get(media_id)
        if artifact is None or artifact.type != "image" or artifact.status != "ready" or not artifact.url:
            return "not_ready"
        inspection = self.inspections.get(media_id)
        if (
            inspection is None
            or inspection.parent_media_id != artifact.parent_media_id
            or inspection.verdict != "revise"
            or (inspection_id is not None and inspection.inspection_id != inspection_id)
        ):
            return "inspection"
        if self.current_versions.get(artifact.goal_id) != media_id or artifact.image_plan is None:
            return "stale"
        return None

    def delivery_versions(self) -> list[MediaArtifact]:
        """每个目标交付一个版本。已受理的修正版可原位回填，明确失败或无改善时保留此前的成品。"""
        selected: dict[str, MediaArtifact] = {}
        for artifact in self.artifacts.values():
            if artifact.goal_id not in self.required_goals or artifact.bound_message_id is not None:
                continue
            current = selected.get(artifact.goal_id)
            review = self.inspections.get(artifact.media_id)
            accepted_revision = artifact.status == "pending" and artifact.job_id is not None
            if (
                current
                and current.status == "ready"
                and not accepted_revision
                and (artifact.status != "ready" or not artifact.url or (review and review.verdict == "stalled"))
            ):
                continue
            selected[artifact.goal_id] = artifact
        return list(selected.values())

    def text_reply_media(self) -> list[dict[str, str]]:
        return [
            {"type": artifact.type, "url": artifact.url}
            for artifact in self.delivery_versions()
            if artifact.status == "ready" and artifact.url
        ]
