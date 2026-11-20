"""伙伴作息的时间推导、交流接续与舞台更新。"""

import hashlib
import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from infrastructure.text import utc_now
from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from runtime.events.emit import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import active_account_ids
from features.companion.definition import parse_persona_definition
from features.companion.models import CompanionRhythm, Persona
from features.configuration.settings.timezone import resolve_user_timezone
from features.conversation.evidence import call_last_seen_at, submission_last_active_at
from features.media.visual_semantics import ActivityType

ContactKind = Literal["message", "call"]
_WAKE_LIFETIME = timedelta(seconds=45)
_MESSAGE_QUIET = timedelta(minutes=10)
_REVIEW_GRACE = timedelta(minutes=2)
_CONTACTS: set[tuple[int, str]] = set()


class RoutineHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bedtime: time
    rise_time: time

    @model_validator(mode="after")
    def valid_wall_times(self) -> "RoutineHours":
        if any(t.tzinfo is not None or t.second or t.microsecond for t in (self.bedtime, self.rise_time)):
            raise ValueError("Use local hours and minutes")
        hours = (self.rise_time.hour * 60 + self.rise_time.minute - self.bedtime.hour * 60 - self.bedtime.minute) % 1440
        if not 180 <= hours <= 960:
            raise ValueError("The sleep period must span 3–16 hours")
        return self


DEFAULT_ROUTINE = RoutineHours(bedtime=time(23), rise_time=time(7))


class BedtimeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: int = Field(strict=True)
    reason: str = Field(min_length=1, max_length=200)


class _Adjustment(BedtimeChange):
    local_day: date
    timezone: str


def _valid_review_minutes(minutes: int) -> int:
    if minutes > (datetime.max.replace(tzinfo=UTC) - utc_now()) // timedelta(minutes=1):
        raise ValueError("The next review time is outside the supported calendar")
    return minutes


ReviewMinutes = Annotated[int, Field(gt=0, strict=True), AfterValidator(_valid_review_minutes)]


class NightChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["sleep", "stay", "activity"]
    activity: str | None = Field(default=None, min_length=1, max_length=200)
    activity_type: Literal["reading", "thinking", "organizing", "creating", "working"] | None = None
    next_review_minutes: ReviewMinutes | None = None

    @model_validator(mode="after")
    def valid_choice(self) -> "NightChoice":
        if self.choice == "activity":
            if not (self.activity and self.activity.strip() and self.activity_type):
                raise ValueError("An activity needs a description and type")
        elif self.activity or self.activity_type:
            raise ValueError("Only an activity choice has an activity")
        if (self.choice == "sleep") != (self.next_review_minutes is None):
            raise ValueError("An awake choice needs a next review time")
        return self


class WakeTransition(BaseModel):
    id: str
    reason: Literal["interaction", "morning"]
    occurred_at: AwareDatetime
    expires_at: AwareDatetime


class RhythmView(BaseModel):
    state: Literal["awake", "asleep"] = "awake"
    timezone: str | None = None
    next_change_at: AwareDatetime | None = None
    revision: int = 0
    wake: WakeTransition | None = None
    interacting: bool = False
    night_activity_type: ActivityType | None = None
    night_activity: str | None = None


class Contact(BaseModel):
    id: str
    kind: ContactKind
    began_at: AwareDatetime
    last_active_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    awake_until: AwareDatetime | None = None
    review_at: AwareDatetime | None = None
    woke_at: AwareDatetime | None = None
    wake_id: str | None = None
    skip_morning_at: AwareDatetime | None = None
    activity_type: ActivityType | None = None
    activity: str | None = None


@dataclass(frozen=True, slots=True)
class _Window:
    local_day: date
    start: datetime
    end: datetime


def _boundary(local_day: date, wall: time, zone: ZoneInfo, *, fold: int = 0) -> datetime:
    # 先转 UTC；跳时日的不存在时刻随缺口顺延，重复时刻的起床取后一次。
    value = datetime.combine(local_day, wall, zone).replace(fold=fold)
    instant = value.astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) < value.replace(tzinfo=None):
        return value.replace(fold=0).astimezone(UTC)
    return instant


def _windows(
    now: datetime, zone: ZoneInfo, routine: RoutineHours, adjustment: _Adjustment | None
) -> tuple[_Window, _Window]:
    local_day = now.astimezone(zone).date()
    windows = []
    for offset in (-2, -1, 0, 1):
        day = local_day + timedelta(days=offset)
        wake_day = day + timedelta(days=int(routine.rise_time <= routine.bedtime))
        start = _boundary(day, routine.bedtime, zone)
        if adjustment and day == adjustment.local_day and zone.key == adjustment.timezone:
            start += timedelta(minutes=adjustment.minutes)
        windows.append(_Window(day, start, _boundary(wake_day, routine.rise_time, zone, fold=1)))
    return (
        max((window for window in windows if window.start <= now), key=lambda window: window.start),
        min((window for window in windows if window.start > now), key=lambda window: window.start),
    )


def _routine(row: CompanionRhythm | None) -> RoutineHours:
    return RoutineHours.model_validate(row.schedule_json) if row and row.schedule_json else DEFAULT_ROUTINE


def _adjustment(row: CompanionRhythm | None) -> _Adjustment | None:
    return _Adjustment.model_validate(row.adjustment_json) if row and row.adjustment_json else None


def _contact(row: CompanionRhythm | None) -> Contact | None:
    return Contact.model_validate(row.contact_json) if row and row.contact_json else None


def track_contact(user_id: int, contact_id: str) -> None:
    _CONTACTS.add((user_id, contact_id))


def untrack_contact(user_id: int, contact_id: str) -> None:
    _CONTACTS.discard((user_id, contact_id))


def _active(user_id: int, contact: Contact) -> bool:
    return contact.ended_at is None and (user_id, contact.id) in _CONTACTS


def _awake_at(user_id: int, contact: Contact | None, at: datetime) -> bool:
    return bool(
        contact
        and contact.began_at <= at
        and (
            _active(user_id, contact)
            or (contact.ended_at is not None and at < contact.ended_at)
            or (contact.awake_until is not None and at < contact.awake_until)
        )
    )


def _view(user_id: int, row: CompanionRhythm | None, zone_name: str | None, now: datetime) -> RhythmView:
    view = RhythmView(timezone=zone_name, revision=row.revision if row else 0)
    if zone_name is None:
        return view
    window, next_window = _windows(now, ZoneInfo(zone_name), _routine(row), _adjustment(row))
    contact = _contact(row)
    held = _awake_at(user_id, contact, now)
    overnight = now < window.end
    if overnight:
        view.state = "awake" if held else "asleep"
        view.next_change_at = window.end
        if held and contact:
            if contact.awake_until is not None:
                view.next_change_at = min(view.next_change_at, contact.awake_until)
            view.interacting = contact.activity_type is None
            view.night_activity, view.night_activity_type = contact.activity, contact.activity_type
            if contact.woke_at and contact.woke_at >= window.start and now < contact.woke_at + _WAKE_LIFETIME:
                view.wake = WakeTransition(
                    id=contact.wake_id or contact.id,
                    reason="interaction",
                    occurred_at=contact.woke_at,
                    expires_at=contact.woke_at + _WAKE_LIFETIME,
                )
    else:
        view.next_change_at = next_window.start
        if (
            now < window.end + _WAKE_LIFETIME
            and (contact is None or contact.skip_morning_at != window.end)
            and not _awake_at(user_id, contact, window.end - timedelta(microseconds=1))
        ):
            view.wake = WakeTransition(
                id=f"morning:{window.end.isoformat()}",
                reason="morning",
                occurred_at=window.end,
                expires_at=window.end + _WAKE_LIFETIME,
            )
    return view


async def read_rhythm(db: AsyncSession, user_id: int, *, now: datetime | None = None, lock: bool = False) -> RhythmView:
    """按绝对时间推导当前状态，页面恢复不等待下一次后台扫描。"""
    row = await _lock_rhythm(db, user_id) if lock else await db.get(CompanionRhythm, user_id)
    return _view(user_id, row, await resolve_user_timezone(db, user_id), now or utc_now())


class RhythmPromptContext(BaseModel):
    state: Literal["awake", "asleep"]
    recent_wake_at: AwareDatetime | None = None
    night_activity: str | None = None
    usual_bedtime: str
    usual_rise_time: str


async def rhythm_prompt_context(db: AsyncSession, user_id: int) -> RhythmPromptContext:
    now = utc_now()
    row = await db.get(CompanionRhythm, user_id)
    view = _view(user_id, row, await resolve_user_timezone(db, user_id), now)
    contact = _contact(row)
    woke_at = contact.woke_at if contact and view.interacting else None
    routine = _routine(row)
    return RhythmPromptContext(
        state=view.state,
        recent_wake_at=woke_at if woke_at and now - woke_at < timedelta(minutes=15) else None,
        night_activity=view.night_activity,
        usual_bedtime=routine.bedtime.strftime("%H:%M"),
        usual_rise_time=routine.rise_time.strftime("%H:%M"),
    )


async def _lock_rhythm(db: AsyncSession, user_id: int) -> CompanionRhythm:
    row = await db.get(CompanionRhythm, user_id, with_for_update=True, populate_existing=True)
    if row is not None:
        return row
    await db.execute(insert(CompanionRhythm).values(user_id=user_id, next_check_at=utc_now()).on_conflict_do_nothing())
    row = await db.get(CompanionRhythm, user_id, with_for_update=True, populate_existing=True)
    assert row is not None
    return row


def _publish(db: AsyncSession, row: CompanionRhythm, zone: str | None, now: datetime, *, changed: bool = False) -> None:
    view = _view(row.user_id, row, zone, now)
    contact = _contact(row)
    deadlines = [view.next_change_at]
    if contact is not None:
        deadlines.append(contact.review_at if contact.awake_until and now < contact.awake_until else None)
        if contact.ended_at is None:
            deadlines.append(now + timedelta(minutes=1))
    row.next_check_at = min((deadline for deadline in deadlines if deadline is not None), default=None)
    key = f"{view.timezone}|{view.state}|{view.next_change_at}|{view.interacting}|{view.night_activity_type}"
    if changed or key != row.projection_key:
        row.projection_key = key
        row.revision += 1
        emit_ws_event(
            db, user_id=row.user_id, event_type="companion.rhythm.updated", payload={"revision": row.revision}
        )


async def begin_contact(db: AsyncSession, user_id: int, contact_id: str, kind: ContactKind) -> None:
    row = await _lock_rhythm(db, user_id)
    previous = _contact(row)
    if previous and previous.id == contact_id:
        return
    now = utc_now()
    zone = await resolve_user_timezone(db, user_id)
    # 短时间内的下一条消息承接同一次苏醒，保持过渡身份稳定。
    old = _view(user_id, row, zone, now)
    woke_at = now if old.state == "asleep" else None
    if previous and old.wake and old.wake.reason == "interaction":
        woke_at = previous.woke_at
    contact = Contact(id=contact_id, kind=kind, began_at=now, woke_at=woke_at)
    if woke_at is not None and old.wake is not None and old.wake.reason == "interaction":
        contact.wake_id = old.wake.id
    if zone and old.wake is None:
        window, _ = _windows(now, ZoneInfo(zone), _routine(row), _adjustment(row))
        if window.end <= now < window.end + _WAKE_LIFETIME:
            contact.skip_morning_at = window.end
    row.contact_json = contact.model_dump(mode="json")
    _publish(db, row, zone, now)
    # 手势在同一事务内开始并结束交流，结束前会重新锁定并读取此行。
    await db.flush([row])


def _finish(row: CompanionRhythm, contact: Contact, zone: str | None, ended_at: datetime) -> None:
    contact.ended_at = ended_at
    if zone:
        window, _ = _windows(ended_at, ZoneInfo(zone), _routine(row), _adjustment(row))
        if ended_at < window.end:
            contact.review_at = ended_at + (_MESSAGE_QUIET if contact.kind == "message" else timedelta(0))
            contact.awake_until = min(window.end, contact.review_at + _REVIEW_GRACE)
    row.contact_json = contact.model_dump(mode="json")


async def finish_contact(db: AsyncSession, user_id: int, contact_id: str) -> None:
    row = await db.get(CompanionRhythm, user_id, with_for_update=True, populate_existing=True)
    contact = _contact(row)
    if row is None or contact is None or contact.id != contact_id or contact.ended_at is not None:
        return
    now = utc_now()
    zone = await resolve_user_timezone(db, user_id)
    _finish(row, contact, zone, now)
    _publish(db, row, zone, now)


async def advance_rhythm(db: AsyncSession, user_id: int) -> Contact | None:
    """推进显示修订并认领最多一次到期的当晚判断。"""
    row = await _lock_rhythm(db, user_id)
    zone = await resolve_user_timezone(db, user_id)
    now = utc_now()
    contact = _contact(row)
    if contact and contact.ended_at is None:
        if not _active(user_id, contact):
            if contact.kind == "call":
                observed = await call_last_seen_at(db, user_id, int(contact.id.removeprefix("call:")))
            else:
                observed = await submission_last_active_at(db, user_id, contact.id.removeprefix("message:"))
            last_active = max(
                contact.began_at, observed or contact.began_at, contact.last_active_at or contact.began_at
            )
            _finish(row, contact, zone, min(last_active, now))
        elif contact.kind == "message":
            # 长文本回合可能还没有最终回复，保存最近一次确认其仍在执行的时间。
            contact.last_active_at = now
            row.contact_json = contact.model_dump(mode="json")
    _publish(db, row, zone, now)
    if not (
        zone
        and contact
        and contact.review_at
        and contact.review_at <= now
        and contact.awake_until
        and now < contact.awake_until
    ):
        return None
    window, _ = _windows(now, ZoneInfo(zone), _routine(row), _adjustment(row))
    if now >= window.end:
        return None
    contact.review_at = None
    row.contact_json = contact.model_dump(mode="json")
    _publish(db, row, zone, now)
    return contact


async def due_rhythm_users(db: AsyncSession, now: datetime, online: Collection[int]) -> set[int]:
    eligible = select(Persona.user_id).where(Persona.is_complete.is_(True), Persona.user_id.in_(active_account_ids()))
    due = select(CompanionRhythm.user_id).where(
        CompanionRhythm.next_check_at <= now, CompanionRhythm.user_id.in_(eligible)
    )
    if not online:
        return set(await db.scalars(due))
    new = eligible.where(
        Persona.user_id.in_(online),
        ~select(CompanionRhythm.user_id).where(CompanionRhythm.user_id == Persona.user_id).exists(),
    )
    return set(await db.scalars(due.union_all(new)))


async def reschedule_timezone_change(db: AsyncSession, user_id: int) -> None:
    await db.execute(update(CompanionRhythm).where(CompanionRhythm.user_id == user_id).values(next_check_at=utc_now()))


async def settle_night_choice(db: AsyncSession, user_id: int, claimed: Contact, choice: NightChoice) -> None:
    row = await _lock_rhythm(db, user_id)
    contact = _contact(row)
    now = utc_now()
    zone = await resolve_user_timezone(db, user_id)
    if not (zone and contact == claimed and contact.awake_until and now < contact.awake_until):
        return
    window, _ = _windows(now, ZoneInfo(zone), _routine(row), _adjustment(row))
    if now >= window.end:
        return
    contact.activity, contact.activity_type = choice.activity, choice.activity_type
    next_review = now + timedelta(minutes=choice.next_review_minutes or 0)
    if choice.choice != "sleep" and next_review < window.end:
        contact.review_at = next_review
        next_review += _REVIEW_GRACE
    contact.awake_until = min(window.end, next_review)
    row.contact_json = contact.model_dump(mode="json")
    _publish(db, row, zone, now, changed=True)


async def set_bedtime_change(db: AsyncSession, user_id: int, change: BedtimeChange, *, proposed_at: datetime) -> None:
    now = utc_now()
    zone_name = await resolve_user_timezone(db, user_id)
    if zone_name is None:
        return
    zone = ZoneInfo(zone_name)
    local_day = proposed_at.astimezone(zone).date()
    if local_day != now.astimezone(zone).date():
        return
    row = await _lock_rhythm(db, user_id)
    routine = _routine(row)
    normal_start = _boundary(local_day, routine.bedtime, zone)
    wake_day = local_day + timedelta(days=int(routine.rise_time <= routine.bedtime))
    previous_wake = _boundary(wake_day - timedelta(days=1), routine.rise_time, zone, fold=1)
    next_wake = _boundary(wake_day, routine.rise_time, zone, fold=1)
    minute = timedelta(minutes=1)
    if not (previous_wake - normal_start) // minute < change.minutes < (next_wake - normal_start) // minute:
        return
    start = normal_start + timedelta(minutes=change.minutes)
    if min(normal_start, start) <= now:
        return
    existing = _adjustment(row)
    if (
        existing
        and existing.local_day == local_day
        and existing.timezone == zone_name
        and normal_start + timedelta(minutes=existing.minutes) <= now
    ):
        return
    row.adjustment_json = _Adjustment(local_day=local_day, timezone=zone_name, **change.model_dump()).model_dump(
        mode="json"
    )
    _publish(db, row, zone_name, now)


@dataclass(frozen=True, slots=True)
class RoutineRequest:
    basis: str
    persona: dict[str, str]


async def _routine_request(db: AsyncSession, user_id: int) -> RoutineRequest | None:
    persona = await db.get(Persona, user_id, populate_existing=True)
    if persona is None or not persona.is_complete:
        return None
    definition = parse_persona_definition(persona.definition_json)
    inputs = {key: definition[key] for key in ("personality", "occupation", "background") if definition.get(key)}
    return RoutineRequest(hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(), inputs)


async def claim_routine_refresh(db: AsyncSession, user_id: int) -> RoutineRequest | None:
    target = await _routine_request(db, user_id)
    if target is None:
        return None
    row = await _lock_rhythm(db, user_id)
    now = utc_now()
    if row.schedule_basis == target.basis or (row.retry_schedule_at is not None and row.retry_schedule_at > now):
        return None
    row.retry_schedule_at = now + timedelta(hours=6)
    return target


async def settle_routine_refresh(
    db: AsyncSession, user_id: int, target: RoutineRequest, hours: RoutineHours | None
) -> None:
    row = await _lock_rhythm(db, user_id)
    now = utc_now()
    if await _routine_request(db, user_id) != target:
        row.retry_schedule_at = None
        return
    if hours is None:
        row.retry_schedule_at = now + timedelta(minutes=5)
        return
    row.schedule_json = hours.model_dump(mode="json")
    row.schedule_basis = target.basis
    row.retry_schedule_at = None
    _publish(db, row, await resolve_user_timezone(db, user_id), now, changed=True)
