"""夜间 Stage 3：能力目录驱动的自主规划、持久化动作账本与顺序执行。"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import (
    NIGHTLY_PLANNING_MAX_TOKENS,
    NIGHTLY_PLANNING_REASONING_EFFORT,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    parse_llm_json,
    safe_json_loads,
    utc_now,
)
from modules.auth import User
from modules.companion import (
    BackdropIntent,
    BackdropOrigin,
    BackdropStatus,
    CompanionMoment,
    CompanionOutfit,
    CompanionRoomBackdrop,
    MomentKind,
    Persona,
)
from modules.media import VideoGenJob
from modules.scheduler import NightlyActivityAction, NightlyActivityLog
from modules.settings import UserSetting
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.generation import (
    activate_outfit,
    confirm_outfit,
    create_outfit_draft,
    enqueue_video_job,
    generate_images,
    get_room_state,
    invalidate_room_for_outfit,
    load_avatar_bytes_as_data_uri,
    resolve_image_gen_chain,
    resolve_self_reference_data_uri,
    resume_outfit_split,
    resume_room_generation,
    schedule_room_generation,
)
from services.domains.automation.cron_jobs import create_job, remove_job
from services.domains.journal import create_generated_moment, create_user_moment
from services.infrastructure.assets import save_companion_asset
from services.infrastructure.llm import call_llm_once, resolve_provider_chain, synthesize_speech

logger = get_logger(__name__)

_OUTFIT_WAIT_SECONDS = 30 * 60
_ROOM_WAIT_SECONDS = 15 * 60
_POLL_SECONDS = 3.0
_ROOM_INTENTS = frozenset(intent.value for intent in BackdropIntent)
_IMAGE_SIZES = frozenset(
    (
        "1024x1024",
        "1024x1792",
        "1792x1024",
        "1:1",
        "16:9",
        "4:3",
        "3:2",
        "2:3",
        "3:4",
        "9:16",
        "21:9",
    ),
)
_VIDEO_ASPECT_RATIOS = frozenset(("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"))
_TERMINAL_ACTION_STATUSES = frozenset(
    ("succeeded", "partial", "skipped", "blocked", "failed", "interrupted"),
)
_SUCCESS_ACTION_STATUSES = frozenset(("succeeded", "partial"))
_ACTION_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")
_MAX_ACTIONS = 8


class NightlyCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    phase: int
    description: str
    arguments: dict[str, Any]
    exclusive_group: str | None = None
    paid: bool = False

    def __init__(
        self,
        name: str,
        phase: int,
        description: str,
        arguments: dict[str, Any],
        exclusive_group: str | None = None,
        paid: bool = False,
    ) -> None:
        super().__init__(
            name=name,
            phase=phase,
            description=description,
            arguments=arguments,
            exclusive_group=exclusive_group,
            paid=paid,
        )


class DateContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_date: str
    tomorrow_date: str
    tomorrow_weekday: str
    next_7_days: list[str] = Field(default_factory=list)
    user_timezone: str

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class OutfitWearArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    outfit_id: int | str
    reason: str = ""


class OutfitCreateArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str
    reason: str = ""


class RoomChangeArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: str = "mood"
    notes: str | None = None
    reason: str = ""


class MomentCreateArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    body: str
    emotion: str | None = None
    reason: str = ""


class MediaImageArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str
    title: str
    body: str = ""
    size: str = "1024x1024"
    depicts_self: bool = False
    narration: str | None = None
    reason: str = ""


class MediaVideoArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str
    title: str
    body: str = ""
    duration: int | str = 6
    aspect_ratio: str = "16:9"
    depicts_self: bool = False
    narration: str | None = None
    reason: str = ""


class MediaVoiceArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    title: str
    body: str = ""
    reason: str = ""


class OutreachScheduleArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "主动问候"
    schedule: str
    prompt: str
    reason: str = ""


class PlannedAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    capability: str
    phase: int
    depends_on: list[str] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)


class NormalizedPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 2
    theme: str = ""
    rationale: str = ""
    reveal: str = ""
    actions: list[PlannedAction] = Field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class ActionExecutionResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    capability: str = ""
    fact: str | None = None
    reason: str | None = None
    warning: str | None = None
    dependencies: list[str] | None = None
    outfit_id: int | None = None
    backdrop_id: int | None = None
    moment_id: str | None = None
    job_id: int | None = None
    cron_job_id: str | None = None
    expires_at: str | None = None
    audio_path: str | None = None
    voice_id: str | None = None

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        if self.__pydantic_extra__ and item in self.__pydantic_extra__:
            return self.__pydantic_extra__[item]
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class PlanningResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: str = ""
    rationale: str = ""
    reveal: str = ""
    actions: dict[str, ActionExecutionResult] = Field(default_factory=dict)

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class PlanningPolicies(BaseModel):
    model_config = ConfigDict(extra="ignore")

    outfit: str = "llm_may_replace"
    room: str = "llm_may_replace"
    media: bool = True
    voice: bool = True

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)


class PlanningProviders(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image: bool = False
    image_reference: bool = False
    video: bool = False
    tts: bool = False

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)


class PersonaContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    complete: bool = False
    definition: dict[str, Any] = Field(default_factory=dict)
    current_mood: str | None = None
    render_mode: str | None = None

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class RoomContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    active_brief: str = ""
    generation_pending: bool = False

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)


class WardrobeItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    description: str = ""
    status: str
    active: bool = False
    pending_wear: bool = False

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class RecentActionSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str
    capability: str
    status: str
    fact: str | None = None


class RecentMomentSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str
    kind: str
    title: str
    source: str


class AvailableCapability(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    phase: int
    description: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    exclusive_group: str | None = None
    paid: bool = False

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)


class BlockedCapability(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    reason: str


class PlanningContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    policies: PlanningPolicies
    providers: PlanningProviders
    selected_voice_id: str = ""
    language: str = ""
    persona: PersonaContext
    room: RoomContext
    wardrobe: list[WardrobeItem] = Field(default_factory=list)
    recent_autonomous_actions: list[RecentActionSummary] = Field(default_factory=list)
    recent_moments: list[RecentMomentSummary] = Field(default_factory=list)
    available_capabilities: list[AvailableCapability] = Field(default_factory=list)
    blocked_capabilities: list[BlockedCapability] = Field(default_factory=list)
    plan_theme: str = ""
    action_row_id: int | None = Field(default=None, alias="_action_row_id")
    resume_result: dict[str, Any] | None = Field(default=None, alias="_resume_result")

    def __getitem__(self, item: str) -> Any:
        if item == "_action_row_id":
            return self.action_row_id
        if item == "_resume_result":
            return self.resume_result
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        if item == "_action_row_id":
            return self.action_row_id if self.action_row_id is not None else default
        if item == "_resume_result":
            return self.resume_result if self.resume_result is not None else default
        return getattr(self, item, default)


_CAPABILITIES: tuple[NightlyCapability, ...] = (
    NightlyCapability(
        "outfit.wear",
        10,
        "穿上衣橱中一个 ready 外观。只能使用上下文中列出的 outfit_id。",
        {"outfit_id": "integer", "reason": "string"},
        exclusive_group="outfit",
    ),
    NightlyCapability(
        "outfit.create",
        10,
        "构思并生成一套新外观，完成 2D 切分后自动穿上；只在现有衣橱不合适或特殊节点时使用。",
        {"description": "string", "reason": "string"},
        exclusive_group="outfit",
        paid=True,
    ),
    NightlyCapability(
        "room.change",
        20,
        "重新布置生活空间；若前一步换装，执行器会使用最新外观生成。",
        {
            "intent": "decorate|seasonal|mood|rebuild",
            "notes": "string",
            "reason": "string",
        },
        exclusive_group="room",
        paid=True,
    ),
    NightlyCapability(
        "moment.create",
        30,
        "写下一条没有媒体的生活空间片刻，例如一张便笺、愿望或值得纪念的小事。",
        {"title": "string", "body": "string", "emotion": "string"},
        exclusive_group="text_moment",
    ),
    NightlyCapability(
        "media.image",
        30,
        "创作永久保存到片刻的图片；depicts_self=true 时自动注入角色身份与当前外观参考。可附带 narration 形成会说话的画作。",
        {
            "prompt": "string",
            "title": "string",
            "body": "string",
            "size": "supported image size",
            "depicts_self": "boolean",
            "narration": "optional string",
            "reason": "string",
        },
        paid=True,
    ),
    NightlyCapability(
        "media.video",
        30,
        "创作永久保存到片刻的短视频；depicts_self=true 时以当前全身外观作为首帧，narration 会用角色当前音色生成同步祝福配音。",
        {
            "prompt": "string",
            "title": "string",
            "body": "string",
            "duration": "6|10",
            "aspect_ratio": "supported ratio",
            "depicts_self": "boolean",
            "narration": "optional string",
            "reason": "string",
        },
        paid=True,
    ),
    NightlyCapability(
        "media.voice",
        30,
        "用角色当前音色录制一段语音心意并永久保存到片刻。",
        {"text": "string", "title": "string", "body": "string", "reason": "string"},
        paid=True,
    ),
    NightlyCapability(
        "outreach.schedule",
        40,
        "安排次日主动联系；从计划时间起等待用户在线，最晚保留到用户本地次日结束。",
        {"name": "string", "schedule": "five-field UTC cron", "prompt": "string"},
        exclusive_group="outreach",
    ),
)
_CAPABILITY_BY_NAME = {cap.name: cap for cap in _CAPABILITIES}

_PLANNING_SYSTEM_PROMPT = """You are SpiritAgent's nightly inner decision engine. You have subjective initiative and may use any coherent subset of the capabilities supplied at runtime. This is not a checklist: returning an empty actions list is correct on an ordinary quiet night.

Plan a small, emotionally grounded surprise or preparation for tomorrow only when the relationship context warrants it. Important dates, promises, user preferences, unresolved feelings, seasonal context, and the companion's own mood are strong signals. Recent autonomous history is supplied so you do not repeat yourself or generate paid media too frequently.

Rules:
- Write all user-facing titles, bodies, narration and messages in context.language (Chinese when unset), in the companion's voice. Technical generation prompts may use English.
- Only choose names from available_capabilities. Policies and availability are authoritative.
- Prefer one coherent idea over unrelated actions. Paid actions require a specific reason.
- Preserve the companion's identity. For an image or video depicting the companion, set depicts_self=true; the runtime injects the canonical identity and the newly active outfit.
- If providers.image_reference is false, do not choose a self-depicting image. Outfit and room capabilities are already hidden in that case.
- Use depends_on when an action semantically needs an earlier action. Runtime also enforces the phase order outfit → room → moments/media → outreach.
- A narrated video is the preferred way to make a spoken visual greeting. Keep narration natural and short.
- Outreach is a future-turn instruction, not final dialogue. Its five-field schedule is UTC and its first trigger must fall on tomorrow_date in user_timezone.
- Do not invent weather or calendar events absent from context. You may reason from explicit important dates and supplied Gregorian dates.
- Do not alter core identity, persona definition, files, accounts, or external services: these are intentionally absent from the safe nightly catalog.

Return JSON only:
{
  "theme": "short coherent idea or empty",
  "rationale": "why this is worth doing tonight",
  "reveal": "how tomorrow should feel",
  "actions": [
    {
      "id": "stable_short_id",
      "capability": "one available capability name",
      "depends_on": ["earlier_action_id"],
      "arguments": {}
    }
  ]
}
"""


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _setting_value(rows: dict[str, str], key: str, default: Any) -> Any:
    raw = rows.get(key)
    if raw is None:
        return default
    return safe_json_loads(raw, default=default)


async def _provider_available(
    db: AsyncSession,
    user_id: int,
    service_type: str,
) -> bool:
    try:
        return bool(await resolve_provider_chain(db, user_id, service_type))
    except Exception:
        logger.warning(
            "nightly capability availability check failed",
            extra={"user_id": user_id, "service_type": service_type},
            exc_info=True,
        )
        return False


async def _reference_image_provider_available(
    db: AsyncSession,
    user_id: int,
) -> bool:
    try:
        chain, _ = await resolve_image_gen_chain(db, user_id, "nightly-reference")
        return bool(chain)
    except Exception:
        logger.warning(
            "nightly reference-image capability check failed",
            extra={"user_id": user_id},
            exc_info=True,
        )
        return False


def _capability_availability(
    context: PlanningContext,
) -> tuple[list[AvailableCapability], list[BlockedCapability]]:
    policy = context.policies
    providers = context.providers
    wardrobe = context.wardrobe
    persona_ready = bool(context.persona.complete)
    has_ready_outfit = any(item.status == "ready" for item in wardrobe)
    splitting = any(item.status == "splitting" for item in wardrobe)
    rules: dict[str, tuple[bool, str]] = {
        "outfit.wear": (
            policy.outfit != "locked" and has_ready_outfit,
            "换装已锁定或没有 ready 外观",
        ),
        "outfit.create": (
            policy.outfit != "locked" and providers.image_reference and persona_ready and not splitting,
            "换装已锁定、形象/生图不可用或已有外观正在切分",
        ),
        "room.change": (
            policy.room != "locked"
            and providers.image_reference
            and persona_ready
            and not context.room.generation_pending,
            "房间已锁定、形象/生图不可用或已有房间正在生成",
        ),
        "moment.create": (True, ""),
        "media.image": (
            bool(policy.media) and providers.image,
            "自主心意或生图供应商不可用",
        ),
        "media.video": (
            bool(policy.media) and providers.video,
            "自主心意或视频供应商不可用",
        ),
        "media.voice": (
            bool(policy.voice) and providers.tts,
            "夜间自主语音或 TTS 供应商不可用",
        ),
        "outreach.schedule": (True, ""),
    }
    available: list[AvailableCapability] = []
    blocked: list[BlockedCapability] = []
    for capability in _CAPABILITIES:
        allowed, reason = rules[capability.name]
        if allowed:
            available.append(
                AvailableCapability(
                    name=capability.name,
                    phase=capability.phase,
                    description=capability.description,
                    arguments=dict(capability.arguments),
                    exclusive_group=capability.exclusive_group,
                    paid=capability.paid,
                ),
            )
        else:
            blocked.append(BlockedCapability(name=capability.name, reason=reason))
    return available, blocked


async def _collect_context(user_id: int) -> PlanningContext:
    async with SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        outfits = (
            (
                await db.execute(
                    select(CompanionOutfit)
                    .where(CompanionOutfit.user_id == user_id)
                    .order_by(CompanionOutfit.created_at.desc()),
                )
            )
            .scalars()
            .all()
        )
        room = await get_room_state(db, user_id)
        setting_rows = (
            await db.execute(
                select(UserSetting.setting_key, UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                ),
            )
        ).all()
        settings = {str(key): str(value) for key, value in setting_rows}
        image_available = await _provider_available(db, user_id, "image_gen")
        reference_image_available = await _reference_image_provider_available(db, user_id)
        video_available = await _provider_available(db, user_id, "video_gen")
        tts_available = await _provider_available(db, user_id, "tts")
        recent_actions = (
            (
                await db.execute(
                    select(NightlyActivityAction)
                    .where(NightlyActivityAction.user_id == user_id)
                    .order_by(NightlyActivityAction.id.desc())
                    .limit(40),
                )
            )
            .scalars()
            .all()
        )
        recent_moments = (
            (
                await db.execute(
                    select(CompanionMoment)
                    .where(CompanionMoment.user_id == user_id)
                    .order_by(CompanionMoment.occurred_at.desc())
                    .limit(20),
                )
            )
            .scalars()
            .all()
        )

    definition = safe_json_loads(
        persona.definition_json if persona is not None else "{}",
        default={},
    )
    if not isinstance(definition, dict):
        definition = {}
    active_room = room["active"]
    context = PlanningContext(
        policies=PlanningPolicies(
            outfit=persona.outfit_policy if persona is not None else "llm_may_replace",
            room=room["policy"],
            media=_setting_value(settings, "companion.autonomous_media", True) is True,
            voice=_setting_value(settings, "companion.autonomous_voice", True) is True,
        ),
        providers=PlanningProviders(
            image=image_available,
            image_reference=reference_image_available,
            video=video_available,
            tts=tts_available,
        ),
        selected_voice_id=str(
            _setting_value(settings, "companion.voice_id", "") or "",
        ),
        language=str(_setting_value(settings, "language", "") or ""),
        persona=PersonaContext(
            complete=bool(persona and persona.is_complete),
            definition=definition,
            current_mood=persona.current_mood if persona is not None else None,
            render_mode=persona.render_mode if persona is not None else None,
        ),
        room=RoomContext(
            active_brief=active_room.brief if active_room is not None else "",
            generation_pending=room["pending"] is not None,
        ),
        wardrobe=[
            WardrobeItem(
                id=outfit.id,
                name=outfit.name,
                description=outfit.description or "",
                status=outfit.status,
                active=outfit.active,
                pending_wear=outfit.pending_wear,
            )
            for outfit in outfits
        ],
        recent_autonomous_actions=[
            RecentActionSummary(
                date=row.target_date.isoformat(),
                capability=row.capability,
                status=row.status,
                fact=(row.result or {}).get("fact"),
            )
            for row in recent_actions
        ],
        recent_moments=[
            RecentMomentSummary(
                date=row.occurred_at.date().isoformat(),
                kind=row.kind,
                title=row.title,
                source=row.source,
            )
            for row in recent_moments
        ],
    )
    available, blocked = _capability_availability(context)
    context.available_capabilities = available
    context.blocked_capabilities = blocked
    return context


def _normalize_action_id(raw: Any, index: int, seen: set[str]) -> str:
    candidate = _ACTION_ID_PATTERN.sub("_", _text(raw, 48)).strip("_")
    if not candidate:
        candidate = f"action_{index + 1}"
    base = candidate
    suffix = 2
    while candidate in seen:
        candidate = f"{base}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _normalize_plan(parsed: Any, context: PlanningContext) -> NormalizedPlan:
    if not isinstance(parsed, dict):
        raise TypeError("nightly planning returned invalid JSON")
    available_names = {item.name for item in context.available_capabilities}
    raw_actions = parsed.get("actions")
    if not isinstance(raw_actions, list):
        raw_actions = []
    seen_ids: set[str] = set()
    seen_groups: set[str] = set()
    media_count = 0
    actions: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_actions):
        if len(actions) >= _MAX_ACTIONS:
            break
        if not isinstance(raw, dict):
            continue
        capability_name = _text(raw.get("capability"), 64)
        spec = _CAPABILITY_BY_NAME.get(capability_name)
        if spec is None or capability_name not in available_names:
            continue
        if spec.exclusive_group and spec.exclusive_group in seen_groups:
            continue
        if capability_name.startswith("media."):
            if media_count >= 2:
                continue
            media_count += 1
        action_id = _normalize_action_id(raw.get("id"), index, seen_ids)
        dependencies = [
            _ACTION_ID_PATTERN.sub("_", str(item))[:48] for item in raw.get("depends_on", []) if isinstance(item, str)
        ]
        args = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
        if (
            capability_name == "media.image"
            and args.get("depicts_self") is True
            and not context.providers.image_reference
        ):
            continue
        actions.append(
            {
                "id": action_id,
                "capability": capability_name,
                "phase": spec.phase,
                "depends_on": dependencies,
                "arguments": args,
                "_order": index,
            },
        )
        if spec.exclusive_group:
            seen_groups.add(spec.exclusive_group)
    valid_ids = {item["id"] for item in actions}
    for action in actions:
        action["depends_on"] = [dep for dep in action["depends_on"] if dep in valid_ids and dep != action["id"]]
    actions.sort(key=lambda item: (item["phase"], item["_order"]))
    planned_actions = [
        PlannedAction(
            id=item["id"],
            capability=item["capability"],
            phase=item["phase"],
            depends_on=item["depends_on"],
            arguments=item["arguments"],
        )
        for item in actions
    ]
    return NormalizedPlan(
        version=2,
        theme=_text(parsed.get("theme"), 200),
        rationale=_text(parsed.get("rationale"), 1000),
        reveal=_text(parsed.get("reveal"), 500),
        actions=planned_actions,
    )


async def _stored_plan(log_id: int | None) -> NormalizedPlan | None:
    if log_id is None:
        return None
    async with SESSION_LOCAL() as db:
        log = await db.get(NightlyActivityLog, log_id)
        payload = log.payload if log is not None and isinstance(log.payload, dict) else {}
        plan = payload.get("nightly_plan")
        if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
            try:
                return NormalizedPlan.model_validate(plan)
            except ValidationError:
                return None
        return None


async def _persist_plan(
    log_id: int | None,
    user_id: int,
    target_date: date,
    plan: NormalizedPlan,
) -> None:
    if log_id is None:
        return
    async with SESSION_LOCAL() as db:
        log = await db.get(NightlyActivityLog, log_id)
        if log is None:
            return
        payload = dict(log.payload) if isinstance(log.payload, dict) else {}
        payload["nightly_plan"] = plan.model_dump()
        log.payload = payload
        existing_keys = set(
            (
                await db.execute(
                    select(NightlyActivityAction.action_key).where(
                        NightlyActivityAction.log_id == log_id,
                    ),
                )
            )
            .scalars()
            .all(),
        )
        for action in plan.actions:
            if action.id in existing_keys:
                continue
            db.add(
                NightlyActivityAction(
                    log_id=log_id,
                    user_id=user_id,
                    target_date=target_date,
                    action_key=action.id,
                    capability=action.capability,
                    phase=action.phase,
                    arguments={
                        "depends_on": action.depends_on,
                        "values": action.arguments,
                    },
                ),
            )
        await db.commit()


async def _load_action_rows(log_id: int) -> list[NightlyActivityAction]:
    async with SESSION_LOCAL() as db:
        running = (
            (
                await db.execute(
                    select(NightlyActivityAction).where(
                        NightlyActivityAction.log_id == log_id,
                        NightlyActivityAction.status == "running",
                    ),
                )
            )
            .scalars()
            .all()
        )
        for row in running:
            progress_key = {
                "outfit.create": "outfit_id",
                "room.change": "backdrop_id",
                "media.video": "job_id",
            }.get(row.capability)
            if progress_key and isinstance(row.result, dict) and row.result.get(progress_key):
                # 继续同一业务任务；执行器依据子管线的持久化检查点恢复。
                row.status = "pending"
            else:
                row.status = "interrupted"
                row.finished_at = utc_now()
                row.result = {
                    "status": "interrupted",
                    "reason": "上次进程在动作执行中终止；没有可核对的内部任务 id，为避免重复付费或重复副作用，本动作不自动重放",
                }
        await db.commit()
        return list(
            (
                await db.execute(
                    select(NightlyActivityAction)
                    .where(NightlyActivityAction.log_id == log_id)
                    .order_by(NightlyActivityAction.phase, NightlyActivityAction.id),
                )
            )
            .scalars()
            .all(),
        )


async def _set_action_state(
    action_id: int,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(NightlyActivityAction, action_id)
        if row is None:
            return
        row.status = status
        if status == "running":
            row.started_at = utc_now()
        if status in _TERMINAL_ACTION_STATUSES:
            row.finished_at = utc_now()
        if result is not None:
            row.result = result
        log = await db.get(NightlyActivityLog, row.log_id)
        if log is not None:
            log.updated_at = utc_now()
        await db.commit()


async def _record_executor_state(
    context: PlanningContext,
    status: str,
    result: ActionExecutionResult | dict[str, Any],
) -> None:
    """副作用一落地就写动作账本，缩小执行器返回前进程退出造成的重复窗口。"""
    action_row_id = context.action_row_id
    if isinstance(action_row_id, int):
        data = result.model_dump(exclude_none=True) if isinstance(result, BaseModel) else result
        await _set_action_state(action_row_id, status, data)


async def _wait_for_outfit(user_id: int, outfit_id: int) -> CompanionOutfit | None:
    deadline = monotonic() + _OUTFIT_WAIT_SECONDS
    while monotonic() < deadline:
        async with SESSION_LOCAL() as db:
            outfit = (
                await db.execute(
                    select(CompanionOutfit).where(
                        CompanionOutfit.user_id == user_id,
                        CompanionOutfit.id == outfit_id,
                    ),
                )
            ).scalar_one_or_none()
            if outfit is None or outfit.status in ("failed", "expired"):
                return None
            if outfit.status == "ready":
                return outfit
        await asyncio.sleep(_POLL_SECONDS)
    return None


async def _outfit_policy_allows(user_id: int) -> bool:
    async with SESSION_LOCAL() as db:
        policy = await db.scalar(
            select(Persona.outfit_policy).where(Persona.user_id == user_id),
        )
    return policy != "locked"


async def _execute_outfit_wear(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _outfit_policy_allows(user_id):
        return ActionExecutionResult(status="blocked", reason="outfit policy locked")
    try:
        parsed_args = OutfitWearArgs.model_validate(args)
        outfit_id = int(parsed_args.outfit_id)
    except (ValidationError, TypeError, ValueError):
        return ActionExecutionResult(status="failed", reason="invalid outfit_id")
    listed_ready_ids = {int(item.id) for item in context.wardrobe if item.status == "ready"}
    if outfit_id not in listed_ready_ids:
        return ActionExecutionResult(status="failed", reason="outfit was not a listed ready item")
    if any(int(item.id) == outfit_id and item.active is True for item in context.wardrobe):
        return ActionExecutionResult(status="skipped", reason="outfit is already active")
    async with SESSION_LOCAL() as db:
        outfit = await activate_outfit(db, user_id, outfit_id)
    result = ActionExecutionResult(
        status="succeeded",
        outfit_id=outfit.id,
        fact=f"换上了已有外观「{outfit.name}」",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


async def _execute_outfit_create(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _outfit_policy_allows(user_id):
        return ActionExecutionResult(status="blocked", reason="outfit policy locked")
    try:
        parsed_args = OutfitCreateArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing outfit description")
    description = _text(parsed_args.description, 500)
    if not description:
        return ActionExecutionResult(status="failed", reason="missing outfit description")
    resume_result = context.resume_result
    resume_outfit_id = resume_result.get("outfit_id") if isinstance(resume_result, dict) else None
    try:
        outfit_id = int(resume_outfit_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        async with SESSION_LOCAL() as db:
            draft = await create_outfit_draft(db, user_id, description=description)
            splitting = await confirm_outfit(db, user_id, draft.id)
        outfit_id = splitting.id
        await _record_executor_state(
            context,
            "running",
            ActionExecutionResult(status="running", outfit_id=outfit_id),
        )
    else:
        await resume_outfit_split(user_id, outfit_id)
    ready = await _wait_for_outfit(user_id, outfit_id)
    if ready is None:
        return ActionExecutionResult(
            status="failed",
            outfit_id=outfit_id,
            reason="outfit splitting did not finish active",
        )
    if not ready.active:
        async with SESSION_LOCAL() as db:
            ready = await activate_outfit(db, user_id, ready.id)
    await invalidate_room_for_outfit(user_id, new_fingerprint=str(ready.id))
    display_name = ready.name if ready.name != "新外观" else description[:40]
    result = ActionExecutionResult(
        status="succeeded",
        outfit_id=ready.id,
        fact=f"设计并换上了新外观「{display_name}」",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


async def _wait_for_backdrop(
    user_id: int,
    backdrop_id: int,
) -> CompanionRoomBackdrop | None:
    deadline = monotonic() + _ROOM_WAIT_SECONDS
    while monotonic() < deadline:
        async with SESSION_LOCAL() as db:
            row = (
                await db.execute(
                    select(CompanionRoomBackdrop).where(
                        CompanionRoomBackdrop.user_id == user_id,
                        CompanionRoomBackdrop.id == backdrop_id,
                    ),
                )
            ).scalar_one_or_none()
            if row is None or row.status in (
                BackdropStatus.FAILED.value,
                BackdropStatus.SUPERSEDED.value,
            ):
                return None
            if row.status == BackdropStatus.READY.value:
                active_id = await db.scalar(
                    select(Persona.active_backdrop_id).where(Persona.user_id == user_id),
                )
                return row if active_id == row.id else None
        await asyncio.sleep(_POLL_SECONDS)
    return None


async def _wait_for_room_moment(user_id: int, media_path: str) -> str | None:
    deadline = monotonic() + 5.0
    while monotonic() < deadline:
        async with SESSION_LOCAL() as db:
            moment_id = await db.scalar(
                select(CompanionMoment.id)
                .where(
                    CompanionMoment.user_id == user_id,
                    CompanionMoment.source == "nightly",
                    CompanionMoment.media_url == media_path,
                )
                .order_by(CompanionMoment.occurred_at.desc())
                .limit(1),
            )
        if moment_id:
            return str(moment_id)
        await asyncio.sleep(0.25)
    return None


async def _execute_room_change(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    try:
        parsed_args = RoomChangeArgs.model_validate(args)
    except ValidationError:
        parsed_args = RoomChangeArgs()
    intent = _text(parsed_args.intent, 16)
    if intent not in _ROOM_INTENTS:
        intent = BackdropIntent.MOOD.value
    resume_result = context.resume_result
    resume_backdrop_id = resume_result.get("backdrop_id") if isinstance(resume_result, dict) else None
    try:
        backdrop_id = int(resume_backdrop_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        row = await schedule_room_generation(
            user_id,
            origin=BackdropOrigin.NIGHTLY.value,
            intent=intent,
            notes=_text(parsed_args.notes, 500) or None,
        )
        backdrop_id = row.id
        await _record_executor_state(
            context,
            "running",
            ActionExecutionResult(status="running", backdrop_id=backdrop_id),
        )
    else:
        await resume_room_generation(
            user_id,
            backdrop_id,
            notes=_text(parsed_args.notes, 500) or None,
        )
    ready = await _wait_for_backdrop(user_id, backdrop_id)
    if ready is None:
        return ActionExecutionResult(
            status="failed",
            backdrop_id=backdrop_id,
            reason="room generation did not finish active",
        )
    moment_id = await _wait_for_room_moment(user_id, ready.media_path) if ready.media_path else None
    moment_error = ""
    if moment_id is None and ready.media_path:
        try:
            async with SESSION_LOCAL() as db:
                moment = await create_user_moment(
                    db,
                    user_id,
                    title="房间布置",
                    body=ready.brief,
                    media_url=ready.media_path,
                    kind=MomentKind.SCENE.value,
                    source="nightly",
                )
            moment_id = str(moment.id)
        except Exception as exc:
            moment_error = str(exc)
    result = ActionExecutionResult(
        status="succeeded" if moment_id else "partial",
        backdrop_id=ready.id,
        moment_id=moment_id,
        fact=f"重新布置了房间：{ready.brief}",
        warning=moment_error or None,
    )
    await _record_executor_state(context, result.status, result)
    return result


async def _wait_for_video(user_id: int, job_id: int) -> VideoGenJob | None:
    deadline = monotonic() + float(SETTINGS.video_gen_max_poll_seconds) + 30
    while monotonic() < deadline:
        async with SESSION_LOCAL() as db:
            row = (
                await db.execute(
                    select(VideoGenJob).where(
                        VideoGenJob.user_id == user_id,
                        VideoGenJob.id == job_id,
                    ),
                )
            ).scalar_one_or_none()
            if row is None or row.status in ("failed", "result_unknown"):
                return None
            if row.status == "succeeded":
                return row
        await asyncio.sleep(_POLL_SECONDS)
    return None


async def _current_visual_references(user_id: int) -> tuple[str | None, str | None]:
    identity: str | None = None
    identity_error: Exception | None = None
    try:
        identity = await resolve_self_reference_data_uri(user_id)
    except Exception as exc:  # noqa: BLE001 - 身份素材可能来自多种存储/解码后端，失败类型不封闭
        identity_error = exc
    async with SESSION_LOCAL() as db:
        outfit = (
            await db.execute(
                select(CompanionOutfit)
                .where(
                    CompanionOutfit.user_id == user_id,
                    CompanionOutfit.active.is_(True),
                    CompanionOutfit.status == "ready",
                )
                .order_by(CompanionOutfit.id.desc())
                .limit(1),
            )
        ).scalar_one_or_none()
    outfit_reference = load_avatar_bytes_as_data_uri(outfit.fullbody_url) if outfit is not None else None
    if identity is None and outfit_reference is not None:
        return outfit_reference, None
    if identity is None and identity_error is not None:
        raise identity_error
    return identity, outfit_reference


def _audio_extension(mime: str) -> str:
    normalized = mime.lower()
    if "wav" in normalized:
        return "wav"
    if "ogg" in normalized or "opus" in normalized:
        return "ogg"
    if "mp4" in normalized or "m4a" in normalized:
        return "m4a"
    return "mp3"


async def _voice_asset(
    user_id: int,
    text: str,
    context: PlanningContext,
) -> tuple[str, str]:
    result = await synthesize_speech(
        user_id,
        text,
        context.selected_voice_id,
        context.language,
    )
    path = save_companion_asset(
        result.audio,
        user_id=user_id,
        label="nightly_voice",
        ext=_audio_extension(result.mime),
    )
    return path, result.voice or ""


async def _runtime_media_allowed(
    user_id: int,
    *,
    include_media: bool = True,
    include_voice: bool = False,
) -> bool:
    keys: list[str] = []
    if include_media:
        keys.append("companion.autonomous_media")
    if include_voice:
        keys.append("companion.autonomous_voice")
    async with SESSION_LOCAL() as db:
        rows = (
            await db.execute(
                select(UserSetting.setting_key, UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                    UserSetting.setting_key.in_(keys),
                ),
            )
        ).all()
    values = {str(key): str(value) for key, value in rows}
    return all(_setting_value(values, key, True) is True for key in keys)


async def _runtime_capability_allowed(
    user_id: int,
    capability: str,
    resume_result: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """动作真正执行前重读总控与能力开关，避免长耗时计划期间用户关锁后仍产生副作用。"""
    async with SESSION_LOCAL() as db:
        user = await db.get(User, user_id)
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        room_pending: tuple[int, str] | None = None
        if capability == "room.change":
            room_pending = (
                await db.execute(
                    select(
                        CompanionRoomBackdrop.id,
                        CompanionRoomBackdrop.origin,
                    )
                    .where(
                        CompanionRoomBackdrop.user_id == user_id,
                        CompanionRoomBackdrop.status == BackdropStatus.PENDING.value,
                    )
                    .limit(1),
                )
            ).one_or_none()
        rows = (
            await db.execute(
                select(UserSetting.setting_key, UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                    UserSetting.setting_key.in_(
                        (
                            "companion.autonomous_media",
                            "companion.autonomous_voice",
                        ),
                    ),
                ),
            )
        ).all()
    if user is None or not user.nightly_activity_enabled:
        return False, "nightly activity disabled"
    settings = {str(key): str(value) for key, value in rows}
    if capability.startswith("outfit.") and persona is not None and persona.outfit_policy == "locked":
        return False, "outfit policy locked"
    if capability == "room.change" and persona is not None and persona.backdrop_policy == "locked":
        return False, "room policy locked"
    if capability == "room.change" and room_pending is not None:
        resume_backdrop_id = None
        if isinstance(resume_result, dict):
            with suppress(TypeError, ValueError):
                resume_backdrop_id = int(resume_result.get("backdrop_id"))
        pending_id, pending_origin = room_pending
        if pending_id != resume_backdrop_id and pending_origin != BackdropOrigin.OUTFIT.value:
            return False, "another room generation is already pending"
    if (
        capability in ("media.image", "media.video")
        and _setting_value(settings, "companion.autonomous_media", True) is not True
    ):
        return False, "autonomous media disabled"
    if capability == "media.voice" and _setting_value(settings, "companion.autonomous_voice", True) is not True:
        return False, "autonomous voice disabled"
    return True, ""


async def _optional_narration(
    user_id: int,
    narration: str | None,
    context: PlanningContext,
) -> tuple[str | None, str | None, str | None]:
    cleaned = _text(narration, 800)
    if not cleaned:
        return None, None, None
    resume_result = context.resume_result
    if isinstance(resume_result, dict) and isinstance(resume_result.get("audio_path"), str):
        return resume_result["audio_path"], str(resume_result.get("voice_id") or ""), None
    if not context.providers.tts or not await _runtime_media_allowed(
        user_id,
        include_voice=True,
    ):
        return None, None, "narration unavailable or disabled"
    try:
        audio_path, voice_id = await _voice_asset(user_id, cleaned, context)
        return audio_path, voice_id, None
    except Exception as exc:
        logger.warning(
            "nightly media narration failed",
            extra={"user_id": user_id, "error": str(exc)},
            exc_info=True,
        )
        return None, None, str(exc)


async def _execute_media_image(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _runtime_media_allowed(user_id):
        return ActionExecutionResult(status="blocked", reason="autonomous media disabled")
    try:
        parsed_args = MediaImageArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing image prompt or title")
    prompt = _text(parsed_args.prompt, 4000)
    title = _text(parsed_args.title, 64)
    body = _text(parsed_args.body, 500)
    if not prompt or not title:
        return ActionExecutionResult(status="failed", reason="missing image prompt or title")
    size = _text(parsed_args.size, 16)
    identity = outfit = None
    if parsed_args.depicts_self is True:
        identity, outfit = await _current_visual_references(user_id)
        prompt = "Preserve the exact identity and currently worn outfit in the supplied references. " + prompt
    urls = await generate_images(
        prompt,
        size=size if size in _IMAGE_SIZES else "1024x1024",
        n=1,
        user_id=user_id,
        reference_image=identity,
        secondary_reference_image=outfit,
    )
    audio_path, voice_id, narration_error = await _optional_narration(
        user_id,
        parsed_args.narration,
        context,
    )
    async with SESSION_LOCAL() as db:
        moment = await create_generated_moment(
            db,
            user_id,
            title=title,
            body=body,
            media_url=urls[0],
            media_type="image",
            audio_url=audio_path,
            media_metadata={"voice_id": voice_id} if voice_id else None,
            kind="together",
            source="nightly",
        )
    result = ActionExecutionResult(
        status="succeeded" if narration_error is None else "partial",
        moment_id=str(moment.id),
        fact=f"在片刻相册里准备了图片心意「{title}」",
        warning=narration_error or None,
    )
    await _record_executor_state(context, result.status, result)
    return result


async def _execute_media_video(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _runtime_media_allowed(user_id):
        return ActionExecutionResult(status="blocked", reason="autonomous media disabled")
    try:
        parsed_args = MediaVideoArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing video prompt or title")
    prompt = _text(parsed_args.prompt, 4000)
    title = _text(parsed_args.title, 64)
    body = _text(parsed_args.body, 500)
    if not prompt or not title:
        return ActionExecutionResult(status="failed", reason="missing video prompt or title")
    first_frame = None
    if parsed_args.depicts_self is True:
        identity, outfit = await _current_visual_references(user_id)
        first_frame = outfit or identity
        prompt = (
            "Animate the supplied companion reference while preserving exact identity, clothing, colors, and accessories. "
            + prompt
        )
    try:
        duration = int(parsed_args.duration)
    except (TypeError, ValueError):
        duration = 6
    duration = duration if duration in (6, 10) else 6
    aspect_ratio = _text(parsed_args.aspect_ratio, 16)
    resume_result = context.resume_result
    resume_job_id = resume_result.get("job_id") if isinstance(resume_result, dict) else None
    try:
        job_id = int(resume_job_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        async with SESSION_LOCAL() as db:
            job = await enqueue_video_job(
                db,
                user_id=user_id,
                session_id=None,
                prompt=prompt,
                duration=duration,
                resolution="768P",
                first_frame_image=first_frame,
                model=None,
                aspect_ratio=aspect_ratio if aspect_ratio in _VIDEO_ASPECT_RATIOS else "16:9",
            )
        job_id = job.id
        if job.status == "result_unknown":
            result = ActionExecutionResult(
                status="failed",
                job_id=job_id,
                reason=job.error_message,
            )
            await _record_executor_state(context, "failed", result)
            return result
        await _record_executor_state(
            context,
            "running",
            ActionExecutionResult(status="running", job_id=job_id),
        )
    completed = await _wait_for_video(user_id, job_id)
    if completed is None or not completed.video_url:
        return ActionExecutionResult(
            status="failed",
            job_id=job_id,
            reason="video generation failed or timed out",
        )
    audio_path, voice_id, narration_error = await _optional_narration(
        user_id,
        parsed_args.narration,
        context,
    )
    if audio_path:
        await _record_executor_state(
            context,
            "running",
            ActionExecutionResult(
                status="running",
                job_id=job_id,
                audio_path=audio_path,
                voice_id=voice_id,
            ),
        )
    async with SESSION_LOCAL() as db:
        moment = await create_generated_moment(
            db,
            user_id,
            title=title,
            body=body,
            media_url=completed.video_url,
            media_type="video",
            audio_url=audio_path,
            media_metadata={
                "voice_id": voice_id,
                "narration": _text(parsed_args.narration, 800),
            }
            if voice_id
            else None,
            kind="together",
            source="nightly",
        )
    result = ActionExecutionResult(
        status="succeeded" if narration_error is None else "partial",
        job_id=job_id,
        moment_id=str(moment.id),
        fact=f"在片刻相册里准备了视频心意「{title}」",
        warning=narration_error or None,
    )
    await _record_executor_state(context, result.status, result)
    return result


async def _execute_media_voice(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _runtime_media_allowed(
        user_id,
        include_media=False,
        include_voice=True,
    ):
        return ActionExecutionResult(status="blocked", reason="autonomous voice disabled")
    try:
        parsed_args = MediaVoiceArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing voice text or title")
    spoken = _text(parsed_args.text, 800)
    title = _text(parsed_args.title, 64)
    body = _text(parsed_args.body, 500)
    if not spoken or not title:
        return ActionExecutionResult(status="failed", reason="missing voice text or title")
    audio_path, voice_id = await _voice_asset(user_id, spoken, context)
    async with SESSION_LOCAL() as db:
        moment = await create_generated_moment(
            db,
            user_id,
            title=title,
            body=body,
            media_url=audio_path,
            media_type="audio",
            media_metadata={"voice_id": voice_id, "transcript": spoken},
            kind="together",
            source="nightly",
        )
    result = ActionExecutionResult(
        status="succeeded",
        moment_id=str(moment.id),
        fact=f"在片刻相册里留下了语音心意「{title}」",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


async def _execute_moment_create(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    try:
        parsed_args = MomentCreateArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing moment title or body")
    title = _text(parsed_args.title, 64)
    body = _text(parsed_args.body, 500)
    if not title or not body:
        return ActionExecutionResult(status="failed", reason="missing moment title or body")
    async with SESSION_LOCAL() as db:
        moment = await create_user_moment(
            db,
            user_id,
            title=title,
            body=body,
            emotion=_text(parsed_args.emotion, 32) or None,
            kind="together",
            source="nightly",
        )
    result = ActionExecutionResult(
        status="succeeded",
        moment_id=str(moment.id),
        fact=f"在片刻里写下了「{title}」",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


def _runs_on_target_local_date(
    job: dict[str, Any],
    date_context: DateContext,
) -> bool:
    try:
        next_run = datetime.fromisoformat(str(job["next_run_at"]))
        target = date.fromisoformat(str(date_context.tomorrow_date))
        timezone = ZoneInfo(str(date_context.user_timezone))
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        return False
    return next_run.astimezone(timezone).date() == target


def _outreach_expiry(date_context: DateContext) -> datetime:
    target = date.fromisoformat(str(date_context.tomorrow_date))
    timezone = ZoneInfo(str(date_context.user_timezone))
    return datetime.combine(target + timedelta(days=1), time.min, timezone).astimezone(
        UTC,
    )


def _near_term_cron(now: datetime) -> str:
    target = (now + timedelta(minutes=2)).replace(second=0, microsecond=0)
    return f"{target.minute} {target.hour} {target.day} {target.month} *"


async def _execute_outreach_schedule(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    facts: list[str],
    date_context: DateContext,
) -> ActionExecutionResult:
    async with SESSION_LOCAL() as db:
        user = await db.get(User, user_id)
    if user is None or not user.nightly_activity_enabled:
        return ActionExecutionResult(status="blocked", reason="nightly activity disabled")
    try:
        parsed_args = OutreachScheduleArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing outreach schedule or prompt")
    name = _text(parsed_args.name, 100) or "主动问候"
    schedule = _text(parsed_args.schedule, 100)
    prompt = _text(parsed_args.prompt, 4000)
    if not schedule or not prompt:
        return ActionExecutionResult(status="failed", reason="missing outreach schedule or prompt")
    completed = json.dumps(facts, ensure_ascii=False)
    effective_prompt = f"{prompt}\n\n昨夜已实际完成的自主行动：{completed}。本次夜间主题：{context.plan_theme}。只能自然呼应列表中确实完成的行动，不得声称被跳过、失败或未列出的行动已经完成。"
    job = await create_job(
        user_id=user_id,
        prompt=effective_prompt,
        schedule=schedule,
        name=name,
        deliver="local",
        one_shot=True,
        kind="special",
        expires_at=_outreach_expiry(date_context),
    )
    if job["is_paused"] or not _runs_on_target_local_date(job, date_context):
        await remove_job(user_id, job["id"])
        timezone = ZoneInfo(str(date_context.user_timezone))
        target = date.fromisoformat(str(date_context.tomorrow_date))
        now = utc_now()
        if now.astimezone(timezone).date() != target or now >= _outreach_expiry(date_context):
            return ActionExecutionResult(
                status="failed",
                reason="outreach does not run on target local date",
            )
        job = await create_job(
            user_id=user_id,
            prompt=effective_prompt,
            schedule=_near_term_cron(now),
            name=name,
            deliver="local",
            one_shot=True,
            kind="special",
            expires_at=_outreach_expiry(date_context),
        )
        if job["is_paused"] or not _runs_on_target_local_date(job, date_context):
            await remove_job(user_id, job["id"])
            return ActionExecutionResult(
                status="failed",
                reason="outreach recovery could not find a remaining target-day slot",
            )
    result = ActionExecutionResult(
        status="succeeded",
        cron_job_id=job["id"],
        expires_at=job.get("expires_at"),
        fact="为次日安排了一次主动问候",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


CapabilityExecutor = Callable[
    [int, dict[str, Any], PlanningContext, list[str], DateContext],
    Awaitable[ActionExecutionResult],
]

_EXECUTORS: dict[str, CapabilityExecutor] = {
    "outfit.wear": _execute_outfit_wear,
    "outfit.create": _execute_outfit_create,
    "room.change": _execute_room_change,
    "moment.create": _execute_moment_create,
    "media.image": _execute_media_image,
    "media.video": _execute_media_video,
    "media.voice": _execute_media_voice,
    "outreach.schedule": _execute_outreach_schedule,
}


async def _execute_persisted_actions(
    log_id: int,
    user_id: int,
    context: PlanningContext,
    date_context: DateContext,
) -> dict[str, ActionExecutionResult]:
    rows = await _load_action_rows(log_id)
    by_key = {row.action_key: row for row in rows}
    facts = [
        str((row.result or {}).get("fact"))
        for row in rows
        if row.status in _SUCCESS_ACTION_STATUSES and (row.result or {}).get("fact")
    ]
    results: dict[str, ActionExecutionResult] = {}
    for row in rows:
        if row.status in _TERMINAL_ACTION_STATUSES:
            res_dict = {"capability": row.capability, **(row.result or {"status": row.status})}
            results[row.action_key] = ActionExecutionResult.model_validate(res_dict)
            continue
        dependencies = (row.arguments or {}).get("depends_on", [])
        unsatisfied = [
            dep for dep in dependencies if dep not in by_key or by_key[dep].status not in _SUCCESS_ACTION_STATUSES
        ]
        if unsatisfied:
            result = ActionExecutionResult(
                status="skipped",
                reason="dependency not completed",
                dependencies=unsatisfied,
                capability=row.capability,
            )
            await _set_action_state(row.id, "skipped", result.model_dump(exclude_none=True))
            row.status = "skipped"
            row.result = result.model_dump(exclude_none=True)
            results[row.action_key] = result
            continue
        runtime_allowed, blocked_reason = await _runtime_capability_allowed(
            user_id,
            row.capability,
            row.result if isinstance(row.result, dict) else None,
        )
        if not runtime_allowed:
            result = ActionExecutionResult(
                status="blocked",
                reason=blocked_reason,
                capability=row.capability,
            )
            await _set_action_state(row.id, "blocked", result.model_dump(exclude_none=True))
            row.status = "blocked"
            row.result = result.model_dump(exclude_none=True)
            results[row.action_key] = result
            continue
        executor = _EXECUTORS.get(row.capability)
        if executor is None:
            result = ActionExecutionResult(
                status="blocked",
                reason="capability executor unavailable",
                capability=row.capability,
            )
            await _set_action_state(row.id, "blocked", result.model_dump(exclude_none=True))
        else:
            await _set_action_state(row.id, "running")
            row.status = "running"
            try:
                action_context = context.model_copy(
                    update={
                        "action_row_id": row.id,
                        "resume_result": row.result if isinstance(row.result, dict) else None,
                    },
                )
                result = await executor(
                    user_id,
                    (row.arguments or {}).get("values", {}),
                    action_context,
                    facts,
                    date_context,
                )
            except Exception as exc:
                logger.warning(
                    "nightly capability action failed",
                    extra={
                        "user_id": user_id,
                        "action": row.action_key,
                        "capability": row.capability,
                        "error": str(exc),
                    },
                    exc_info=True,
                )
                result = ActionExecutionResult(status="failed", reason=str(exc))
            status = result.status if result.status in _TERMINAL_ACTION_STATUSES else "failed"
            result.status = status
            result.capability = row.capability
            await _set_action_state(row.id, status, result.model_dump(exclude_none=True))
        row.status = result.status
        row.result = result.model_dump(exclude_none=True)
        if row.status in _SUCCESS_ACTION_STATUSES and result.fact:
            facts.append(result.fact)
        results[row.action_key] = result
    return results


async def _execute_ephemeral_actions(
    plan: NormalizedPlan,
    user_id: int,
    context: PlanningContext,
    date_context: DateContext,
) -> dict[str, ActionExecutionResult]:
    facts: list[str] = []
    results: dict[str, ActionExecutionResult] = {}
    statuses: dict[str, str] = {}
    for action in plan.actions:
        unsatisfied = [dep for dep in action.depends_on if statuses.get(dep) not in _SUCCESS_ACTION_STATUSES]
        if unsatisfied:
            result = ActionExecutionResult(
                status="skipped",
                reason="dependency not completed",
                capability=action.capability,
            )
        else:
            runtime_allowed, blocked_reason = await _runtime_capability_allowed(
                user_id,
                action.capability,
            )
            if not runtime_allowed:
                result = ActionExecutionResult(
                    status="blocked",
                    reason=blocked_reason,
                    capability=action.capability,
                )
            else:
                try:
                    result = await _EXECUTORS[action.capability](
                        user_id,
                        action.arguments,
                        context,
                        facts,
                        date_context,
                    )
                    result.capability = action.capability
                except Exception as exc:  # noqa: BLE001 - 临时执行模式同样要求单动作故障隔离
                    result = ActionExecutionResult(
                        status="failed",
                        reason=str(exc),
                        capability=action.capability,
                    )
        statuses[action.id] = result.status
        if result.status in _SUCCESS_ACTION_STATUSES and result.fact:
            facts.append(result.fact)
        results[action.id] = result
    return results


async def run_nightly_planning(
    llm_cfg: dict[str, Any],
    user_id: int,
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    user_profile: dict[str, str],
    recall_highlights: list[dict[str, Any]],
    date_context: DateContext | dict[str, Any],
    anomaly_stats: dict[str, Any],
    today_conversations: list[dict[str, str]],
    *,
    log_id: int | None = None,
) -> PlanningResult:
    date_ctx = date_context if isinstance(date_context, DateContext) else DateContext.model_validate(date_context)
    context = await _collect_context(user_id)
    plan = await _stored_plan(log_id)
    if plan is None:
        payload = {
            "inferred_profile": inferred_profile,
            "auto_inject_state": auto_inject,
            "user_profile": user_profile,
            "recall_highlights": recall_highlights,
            "today_conversations": today_conversations,
            "autonomous_context": context.model_dump(
                exclude={"action_row_id", "resume_result"},
                exclude_none=True,
            ),
            **date_ctx.model_dump(),
            **anomaly_stats,
        }
        raw = await call_llm_once(
            llm_cfg,
            _PLANNING_SYSTEM_PROMPT,
            payload,
            max_output_tokens=NIGHTLY_PLANNING_MAX_TOKENS,
            reasoning_effort=NIGHTLY_PLANNING_REASONING_EFFORT,
        )
        plan = _normalize_plan(parse_llm_json(raw), context)
        await _persist_plan(
            log_id,
            user_id,
            date.fromisoformat(str(date_ctx.source_date)),
            plan,
        )
    context.plan_theme = plan.theme
    if log_id is not None:
        actions = await _execute_persisted_actions(
            log_id,
            user_id,
            context,
            date_ctx,
        )
    else:
        actions = await _execute_ephemeral_actions(plan, user_id, context, date_ctx)
    return PlanningResult(
        theme=plan.theme,
        rationale=plan.rationale,
        reveal=plan.reveal,
        actions=actions,
    )
