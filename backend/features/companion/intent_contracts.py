from datetime import datetime, timedelta
from typing import Literal, Self

from infrastructure.api_ids import ResourceId
from infrastructure.text import utc_now
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from runtime.presentation import PresentationMode, VisualSurface

CompanionWakeEvent = Literal["desktop_available", "context_changed"]
CompanionIntentStatus = Literal["waiting", "queued", "running", "completed", "cancelled", "expired", "failed"]
MAX_COMPANION_FAILURES: int = 3


class ContactWindow(BaseModel):
    """两个偏移共用一次决定时刻；后续任务只保存换算后的绝对时间。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    after_seconds: int | None = Field(ge=0, description="从本次决定到适合复查的秒数")
    expires_seconds: int = Field(gt=0, description="从同一决定到事情失效的秒数，须晚于复查")

    def bounds(self, decided_at: datetime) -> tuple[datetime, datetime]:
        try:
            return decided_at + timedelta(seconds=self.after_seconds or 0), decided_at + timedelta(
                seconds=self.expires_seconds
            )
        except OverflowError as exc:
            raise ValueError("Contact window falls outside the supported calendar") from exc

    @model_validator(mode="after")
    def valid_expiry(self) -> Self:
        if self.after_seconds is not None and self.expires_seconds <= self.after_seconds:
            raise ValueError("Expiry must be later than the scheduled wake time")
        self.bounds(utc_now())
        return self


class DeferredReply(ContactWindow):
    after_seconds: int = Field(gt=0)
    purpose: str = Field(min_length=1, max_length=500)


class CompanionWaitRequest(ContactWindow):
    intent: str = Field(min_length=1, max_length=4000)
    after_seconds: int | None = Field(default=None, gt=0)
    wake_on: CompanionWakeEvent | None = None

    @model_validator(mode="after")
    def has_wake_condition(self) -> Self:
        if self.after_seconds is None and self.wake_on is None:
            raise ValueError("Provide after_seconds or wake_on")
        return self


class CompanionIntentView(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: ResourceId
    source_kind: str | None = Field(default=None, exclude=True)
    life_event_id: ResourceId | None = Field(default=None, exclude=True)
    intent: str = Field(min_length=1)
    status: CompanionIntentStatus
    not_before_at: datetime
    wake_at: datetime | None
    wake_event: CompanionWakeEvent | None
    expires_at: datetime
    failure_count: int = Field(ge=0, le=MAX_COMPANION_FAILURES)
    last_error: str | None


class CompanionIntentPage(BaseModel):
    intents: list[CompanionIntentView]
    next_cursor: str | None


class CompanionSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: StrictBool
    presentation_mode: PresentationMode
    visual_surface: VisualSurface | None = None
    event: CompanionWakeEvent | None = None

    @model_validator(mode="after")
    def valid_visual_surface(self) -> Self:
        if (self.visual_surface == "desktop" and self.presentation_mode != "desktop") or (
            self.visual_surface in ("life", "sprite") and self.presentation_mode != "window"
        ):
            raise ValueError("Visual surface does not belong to the presentation mode")
        return self


class CompanionTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent_id: ResourceId
    lease_token: str = Field(min_length=1, max_length=64)
