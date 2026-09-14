from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

CompanionWakeEvent = Literal["desktop_available", "context_changed"]
CompanionIntentStatus = Literal["waiting", "queued", "running", "completed", "cancelled", "expired", "failed"]
MAX_COMPANION_FAILURES: int = 3


class CompanionWaitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    intent: str = Field(min_length=1, max_length=4000)
    after_seconds: int | None = Field(default=None, ge=60, le=2_592_000)
    wake_on: CompanionWakeEvent | None = None
    expires_seconds: int = Field(default=86400, ge=120, le=2_592_000)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.after_seconds is None and self.wake_on is None:
            raise ValueError("Provide after_seconds or wake_on")
        if self.after_seconds is not None and self.after_seconds >= self.expires_seconds:
            raise ValueError("Expiry must be later than the scheduled wake time")
        return self


class CompanionIntentView(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: int
    intent: str = Field(min_length=1)
    status: CompanionIntentStatus
    not_before_at: datetime
    wake_at: datetime | None
    wake_event: CompanionWakeEvent | None
    expires_at: datetime
    failure_count: int = Field(ge=0, le=MAX_COMPANION_FAILURES)
    last_error: str | None


class CompanionSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: StrictBool
    event: CompanionWakeEvent | None = None


class CompanionTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent_id: int = Field(gt=0)
    lease_token: str = Field(min_length=1, max_length=64)
