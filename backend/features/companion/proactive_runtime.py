import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from infrastructure.config import SETTINGS
from pydantic import BaseModel, ConfigDict
from runtime.presentation import PresentationMode
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.intent_models import CompanionIntent

PRESENCE_TTL_SECONDS: int = 90
_USER_PROACTIVE_STATE: dict[int, "UserProactiveRecord"] = {}


@dataclass
class UserProactiveRecord:
    last_outreach_ts: float = 0.0
    last_reaction_ts: float = 0.0
    reaction_pending: bool = False
    last_user_contact_ts: float = 0.0
    contact_revision: int = 0
    busy_count: int = 0
    available: bool = False
    observed_at: float = 0.0
    presentation_mode: PresentationMode | None = None
    presentation_revision: int = 0


class PresentationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: PresentationMode
    revision: int


def get_presentation_snapshot(user_id: int) -> PresentationSnapshot | None:
    rec = get_user_proactive_record(user_id)
    if rec.presentation_mode is None or time.monotonic() - rec.observed_at > PRESENCE_TTL_SECONDS:
        return None
    return PresentationSnapshot(mode=rec.presentation_mode, revision=rec.presentation_revision)


def get_user_proactive_record(user_id: int) -> UserProactiveRecord:
    return _USER_PROACTIVE_STATE.setdefault(user_id, UserProactiveRecord())


def clear_user_proactive_state(user_id: int) -> None:
    _USER_PROACTIVE_STATE.pop(user_id, None)


@contextmanager
def user_turn_activity(user_id: int, *, enabled: bool) -> Iterator[None]:
    rec = get_user_proactive_record(user_id)
    if enabled:
        rec.last_user_contact_ts = time.monotonic()
        rec.contact_revision += 1
        rec.busy_count += 1
    try:
        yield
    finally:
        if enabled:
            rec.busy_count -= 1
            rec.last_user_contact_ts = time.monotonic()


def note_outreach_throttle(user_id: int) -> None:
    get_user_proactive_record(user_id).last_outreach_ts = time.monotonic()


async def has_recent_contact_review(db: AsyncSession, user_id: int, since: datetime) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    CompanionIntent.user_id == user_id,
                    CompanionIntent.source_kind == "checkin",
                    CompanionIntent.created_at >= since,
                )
            )
        )
    )


def observe_companion_presence(user_id: int, available: bool, *, presentation_mode: PresentationMode | None) -> bool:
    rec = get_user_proactive_record(user_id)
    now = time.monotonic()
    became_available = available and (not rec.available or now - rec.observed_at > PRESENCE_TTL_SECONDS)
    if (
        rec.available != available
        or rec.presentation_mode != presentation_mode
        or now - rec.observed_at > PRESENCE_TTL_SECONDS
    ):
        rec.presentation_revision += 1
    rec.available = available
    rec.observed_at = now
    rec.presentation_mode = presentation_mode
    return became_available


def can_start_companion_turn(user_id: int) -> bool:
    rec = get_user_proactive_record(user_id)
    now = time.monotonic()
    return (
        rec.available
        and now - rec.observed_at <= PRESENCE_TTL_SECONDS
        and rec.busy_count == 0
        and (
            rec.last_user_contact_ts == 0 or now - rec.last_user_contact_ts >= SETTINGS.companion_contact_quiet_seconds
        )
    )
