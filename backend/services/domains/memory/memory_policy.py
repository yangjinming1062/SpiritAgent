from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemoryCategory = Literal[
    "user_preference",
    "likes",
    "dislikes",
    "key_constraints",
    "other",
    "tool_quirk",
    "environment",
]


class EvidenceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message_id: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=1000)
    stance: Literal["supports", "opposes"]


class MemoryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    memory_id: int | None = Field(default=None, gt=0)
    expected_version: int | None = Field(default=None, gt=0)
    content: str = Field(min_length=1, max_length=2000)
    topic: str = Field(min_length=1, max_length=200)
    category: MemoryCategory
    basis: Literal["explicit", "inferred", "observed"]
    status: Literal["candidate", "active", "invalidated", "forgotten"]
    usage: Literal["contextual", "background"]
    reason: str = Field(min_length=1, max_length=2000)
    expires_at: str | None = None
    evidence: list[EvidenceQuote] = Field(max_length=32)

    @model_validator(mode="after")
    def validate_decision(self) -> "MemoryDecision":
        if (self.memory_id is None) != (self.expected_version is None):
            raise ValueError("memory_id and expected_version must be supplied together")
        if not self.content.strip() or not self.topic.strip() or not self.reason.strip():
            raise ValueError("content, topic and reason must not be blank")
        if self.status != "invalidated" and not any(e.stance == "supports" for e in self.evidence):
            raise ValueError("A retained claim requires supporting original evidence")
        if self.status in {"invalidated", "forgotten"} and self.memory_id is None:
            raise ValueError("Only an existing memory can be invalidated")
        if self.usage == "background" and (self.status != "active" or self.basis != "explicit"):
            raise ValueError("Background requires an active explicit claim")
        if (
            (self.status == "candidate" or self.basis == "inferred")
            and self.status not in {"invalidated", "forgotten"}
            and not self.expires_at
        ):
            raise ValueError("Candidates and inferred patterns require an expiry")
        if self.expires_at is not None:
            value = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if value.tzinfo is None:
                raise ValueError("Expiry must include a timezone")
        return self


class MemoryDecisions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decisions: list[MemoryDecision] = Field(max_length=24)
