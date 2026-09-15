import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from components import SETTINGS, safe_json_loads
from modules.companion import Persona
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PRESENCE_TTL_SECONDS: int = 90
_USER_PROACTIVE_STATE: dict[int, "UserProactiveRecord"] = {}


@dataclass
class UserProactiveRecord:
    last_outreach_ts: float = 0.0
    last_user_contact_ts: float = 0.0
    contact_revision: int = 0
    busy_count: int = 0
    available: bool = False
    observed_at: float = 0.0


def get_user_proactive_record(user_id: int) -> UserProactiveRecord:
    return _USER_PROACTIVE_STATE.setdefault(user_id, UserProactiveRecord())


def clear_user_proactive_state(user_id: int) -> None:
    _USER_PROACTIVE_STATE.pop(user_id, None)


def note_user_contact(user_id: int) -> None:
    rec = get_user_proactive_record(user_id)
    rec.last_user_contact_ts = time.monotonic()
    rec.contact_revision += 1


@contextmanager
def user_turn_activity(user_id: int, *, enabled: bool) -> Iterator[None]:
    rec = get_user_proactive_record(user_id)
    if enabled:
        note_user_contact(user_id)
        rec.busy_count += 1
    try:
        yield
    finally:
        if enabled:
            rec.busy_count -= 1
            rec.last_user_contact_ts = time.monotonic()


def note_outreach_throttle(user_id: int) -> None:
    get_user_proactive_record(user_id).last_outreach_ts = time.monotonic()


def observe_companion_presence(user_id: int, available: bool) -> bool:
    rec = get_user_proactive_record(user_id)
    now = time.monotonic()
    became_available = available and (not rec.available or now - rec.observed_at > PRESENCE_TTL_SECONDS)
    rec.available = available
    rec.observed_at = now
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


async def get_personality_tags(db: AsyncSession, user_id: int) -> list[str]:
    raw = (await db.execute(select(Persona.personality_tags_json).where(Persona.user_id == user_id))).scalar()
    parsed = safe_json_loads(raw, default=[]) if raw else []
    return [tag for tag in parsed if isinstance(tag, str)] if isinstance(parsed, list) else []
