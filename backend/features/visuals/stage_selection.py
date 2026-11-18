"""依据已复核的画面内容，为当前作息和生活情境选择舞台素材。"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from infrastructure.timezone import parse_timezone

from features.companion.rhythm import RhythmView
from features.life.schemas import LifeStateView
from features.media.visual_semantics import ActivityType, DayPhase

from .schemas import AmbientCatalogEntry, SceneMediaRef, StageIntent

_ACTIVITY_TAGS = {
    "idle": ["idle"],
    "reading": ["reading", "working"],
    "working": ["working"],
    "creating": ["creating", "working"],
    "thinking": ["thinking"],
    "resting": ["resting"],
    "organizing": ["organizing", "working"],
    "socializing": ["socializing", "interaction"],
}


@dataclass(frozen=True, slots=True)
class StageMediaCandidate:
    entry: AmbientCatalogEntry
    scene: SceneMediaRef | None
    opening_frame_hash: str | None


def project_stage_intent(life: LifeStateView, focus: str = "") -> StageIntent:
    scene_title = life.current_scene.get("title")
    activity = life.activity_type if life.activity_kind != "intent" else "idle"
    return StageIntent(
        activity=life.current_activity or "",
        scene_title=scene_title if isinstance(scene_title, str) else "",
        mood=life.mood or "",
        focus=focus,
        activity_kind=life.activity_kind or "virtual_activity",
        activity_type=activity,
        scene_tags=life.scene_tags,
        visual_tags=_ACTIVITY_TAGS[activity],
        life_revision=life.presentation_revision,
    )


def stage_day_phase(now: datetime, timezone: str | None) -> tuple[DayPhase, datetime | None]:
    zone = parse_timezone(timezone)
    if zone is None:
        return "day", None
    local = now.astimezone(zone)
    for hour, phase in ((5, "night"), (7, "dawn"), (17, "day"), (20, "dusk")):
        if local.hour < hour:
            return phase, datetime.combine(local.date(), time(hour), zone).astimezone(UTC)
    return "night", datetime.combine(local.date() + timedelta(days=1), time(5), zone).astimezone(UTC)


def stage_activity(intent: StageIntent | None, rhythm: RhythmView) -> ActivityType:
    if rhythm.state == "asleep":
        return "resting"
    if rhythm.interacting:
        return "idle"
    if rhythm.night_activity_type is not None:
        return rhythm.night_activity_type
    if intent is None or intent.activity_kind == "intent":
        return "idle"
    return intent.activity_type


def compatible_activity(
    entry: AmbientCatalogEntry, activity: ActivityType, scene_tags: set[str], *, required: bool = True
) -> bool:
    actual = entry.media_semantics
    return bool(
        actual is not None
        and actual.auto_selectable
        and activity not in actual.avoid_activity_types
        and scene_tags.isdisjoint(actual.avoid_scene_tags)
        and (not required or scene_tags.issuperset(actual.scene_tags))
    )


def scene_matches(scene: SceneMediaRef | None, title: str) -> bool:
    return bool(scene is not None and title and scene.title.strip().casefold() == title)


def cinematic_options(
    catalog: list[StageMediaCandidate],
    activity: ActivityType,
    phase: DayPhase,
    intent: StageIntent | None,
    *,
    asleep: bool,
) -> list[StageMediaCandidate]:
    if activity == "idle":
        return []
    title = intent.scene_title.strip().casefold() if intent and not asleep else ""
    intended_tags = set(intent.scene_tags) if intent and not asleep else set()
    matched: list[StageMediaCandidate] = []
    for candidate in catalog:
        entry = candidate.entry
        actual = entry.media_semantics
        if (
            entry.asset_kind != "cinematic"
            or entry.clip_kind == "once"
            or entry.performance_role != "sustained"
            or actual is None
            or actual.activity_type != activity
            or not compatible_activity(entry, activity, intended_tags | set(actual.scene_tags), required=False)
            or not phase_compatible(actual.day_phase, phase, asleep=asleep)
            or (title and not scene_matches(candidate.scene, title))
            or ("sleeping" in actual.semantic_tags) != asleep
            or "waking" in actual.semantic_tags
            or (not asleep and "periodic" not in entry.allowed_triggers)
        ):
            continue
        matched.append(candidate)
    matched.sort(key=lambda candidate: candidate.entry.day_phase != phase)
    return matched


def phase_compatible(media_phase: DayPhase | None, phase: DayPhase, *, asleep: bool) -> bool:
    if asleep and media_phase in ("day", "dusk"):
        return False
    return media_phase is None or media_phase == phase


def actor_options(
    catalog: list[StageMediaCandidate],
    activity: ActivityType,
    scene_tags: set[str],
    intent: StageIntent | None,
    *,
    asleep: bool,
) -> tuple[list[AmbientCatalogEntry], list[AmbientCatalogEntry]]:
    matching: list[AmbientCatalogEntry] = []
    fallback: list[AmbientCatalogEntry] = []
    for candidate in catalog:
        entry = candidate.entry
        actual = entry.media_semantics
        if (
            entry.asset_kind != "actor"
            or entry.clip_kind != "loop"
            or actual is None
            or "waking" in actual.semantic_tags
            or not compatible_activity(entry, activity, scene_tags)
        ):
            continue
        sleeping = "sleeping" in actual.semantic_tags
        if sleeping:
            if asleep and actual.activity_type == "resting" and entry.performance_role == "sustained":
                matching.append(entry)
            continue
        if "periodic" not in entry.allowed_triggers:
            continue
        if entry.performance_role == "sustained" and actual.activity_type == activity:
            (fallback if asleep else matching).append(entry)
        elif not asleep and entry.performance_role == "idle" and actual.activity_type == "idle":
            fallback.append(entry)
    visual_tags = set(intent.visual_tags) if intent is not None and intent.activity_type == activity else set()
    if visual_tags and not asleep:
        matching.sort(
            key=lambda entry: (
                len(visual_tags.intersection(entry.media_semantics.semantic_tags)) if entry.media_semantics else 0
            ),
            reverse=True,
        )
    return matching[:6], fallback[:6]


def paired_wake(
    catalog: list[StageMediaCandidate], sleep_actors: list[AmbientCatalogEntry], scene_tags: set[str]
) -> tuple[AmbientCatalogEntry, AmbientCatalogEntry] | None:
    """找出从真实安睡首帧接续的起身动作。"""

    for sleep in sleep_actors:
        start = sleep.body.continuity_key if sleep.body is not None else None
        if not start:
            continue
        for candidate in catalog:
            entry = candidate.entry
            actual = entry.media_semantics
            if (
                candidate.opening_frame_hash == start
                and entry.asset_kind == "actor"
                and entry.clip_kind == "once"
                and entry.performance_role == "reaction"
                and actual is not None
                and "waking" in actual.semantic_tags
                and compatible_activity(entry, "resting", scene_tags)
            ):
                return sleep, entry
    return None
