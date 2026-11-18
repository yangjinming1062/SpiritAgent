"""即时桌面表达，选择来自当前可播成品与实际互动。"""

from datetime import datetime, timedelta
from time import monotonic
from typing import Literal, Self
from uuid import UUID

from features.companion.disturbance import get_disturbance_tier
from features.companion.motion import get_motion_level
from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.companion.prompt_runtime import load_companion_prompt_context
from features.companion.prompts import REACTION_INSTRUCTIONS
from features.conversation.context_window import load_recent_context_window
from features.visuals.queries import presentation_state
from features.visuals.schemas import AmbientCatalogEntry
from features.visuals.stage_selection import compatible_activity, stage_activity
from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.api_ids import ResourceId
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import resolve_prompt_text, utc_now
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from runtime.events.emit import emit_ws_event

GestureKind = Literal["tap", "drag_released", "chat_opened"]
_GESTURE_TTL = timedelta(seconds=15)
# 同一账户跨窗口共享预算，冷却长于事件有效期，原请求送达不明时不重复推理。
_REACTION_COOLDOWN_SECONDS = 20


class GestureReactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    kind: GestureKind
    occurred_at: AwareDatetime
    count: int = Field(default=1, ge=1, le=10)


class ReactionVisual(BaseModel):
    visual_id: ResourceId
    version: int


class GestureReactionResponse(BaseModel):
    visual: ReactionVisual | None = None
    text: str | None = None


class ReactionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    choice: Literal["none", "move", "say"]
    visual_key: str | None = Field(min_length=1)
    text: str | None = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def match_choice(self) -> Self:
        if bool(self.visual_key) != (self.choice == "move") or bool(self.text) != (self.choice == "say"):
            raise ValueError("Fill only the fields applicable to the choice")
        return self


def _visual_key(item: AmbientCatalogEntry) -> str:
    return f"{item.visual_id}:{item.version}"


async def _decide(
    user_id: int, event: dict[str, str | int], occurred_at: datetime, *, allow_text: bool
) -> GestureReactionResponse:
    presence = get_presentation_snapshot(user_id)
    record = get_user_proactive_record(user_id)
    now = monotonic()
    age = utc_now() - occurred_at
    direct = event["kind"] != "dialogue"
    if (
        presence is None
        or age < -timedelta(seconds=3)
        or age > _GESTURE_TTL
        or record.reaction_pending
        or (record.last_reaction_ts and now - record.last_reaction_ts < _REACTION_COOLDOWN_SECONDS)
        or (direct and record.busy_count > 0)
    ):
        return GestureReactionResponse()
    record.reaction_pending = True
    record.last_reaction_ts = now
    contact_revision = record.contact_revision
    try:
        tier = await get_disturbance_tier(user_id)
        if tier == "still":
            return GestureReactionResponse()
        allow_text = allow_text and presence.mode == "window"
        presentation = await presentation_state(user_id)
        rhythm = presentation.rhythm
        if rhythm is None or rhythm.state == "asleep":
            return GestureReactionResponse()
        motion = await get_motion_level(user_id)
        activity = stage_activity(presentation.intent, rhythm)
        scene_tags = set(presentation.intent.scene_tags if presentation.intent else ())
        trigger = "user_return" if event["kind"] == "chat_opened" else "chat"
        catalog = {
            _visual_key(item): item
            for item in presentation.ambient_catalog
            if motion != "off"
            and item.asset_kind == "actor"
            and item.clip_kind == "once"
            and item.performance_role == "reaction"
            and trigger in item.allowed_triggers
            and compatible_activity(item, activity, scene_tags)
        }
        if not catalog and not allow_text:
            return GestureReactionResponse()
        ctx = await load_companion_prompt_context(user_id, memory_query=str(event.get("user_message", "")))
        config = await load_user_llm_config(user_id, AiTaskKey.companion_chat)
        if ctx is None or not config.is_configured or utc_now() - occurred_at > _GESTURE_TTL:
            return GestureReactionResponse()
        async with SESSION_LOCAL() as db:
            recent_context = await load_recent_context_window(db, user_id) or ""
        shared_life = ctx.life_context.prompt_payload(shared_only=True)

        def parse_output(raw: object) -> ReactionDecision:
            decision = ReactionDecision.model_validate(raw)
            if decision.visual_key and decision.visual_key not in catalog:
                raise ValueError("Select a key from the offered visual catalog")
            if decision.text and not allow_text:
                raise ValueError("The current surface has no text delivery channel")
            return decision

        result = await call_llm_json(
            config,
            resolve_prompt_text(REACTION_INSTRUCTIONS, ctx.language),
            {
                "output_language": ctx.language,
                "event": event,
                "persona": ctx.persona_extras,
                "local_time": ctx.current_time,
                "current_activity": activity,
                "recent_exchange": recent_context[-4000:],
                "memory": ctx.memory.model_dump(mode="json"),
                "shared_life": shared_life,
                "can_say": allow_text,
                "visuals": [
                    {
                        "key": key,
                        "description": item.action_name,
                        "use_when": item.use_when,
                        "avoid_when": item.avoid_when,
                    }
                    for key, item in catalog.items()
                ],
            },
            schema=ReactionDecision.model_json_schema(),
            schema_name="companion_desktop_reaction",
            parse_output=parse_output,
            repair_prompt="Preserve the choice and fill only its applicable fields.",
            preserve_choices=("choice",),
        )
        if (
            get_presentation_snapshot(user_id) != presence
            or record.contact_revision != contact_revision
            or utc_now() - occurred_at > _GESTURE_TTL
            or await get_disturbance_tier(user_id) == "still"
        ):
            return GestureReactionResponse()
        selected = catalog.get(result.visual_key) if result.visual_key else None
        return GestureReactionResponse(
            visual=ReactionVisual(visual_id=selected.visual_id, version=selected.version) if selected else None,
            text=result.text or None,
        )
    finally:
        record.reaction_pending = False


async def decide_desktop_gesture(user_id: int, gesture: GestureReactionRequest) -> GestureReactionResponse:
    return await _decide(
        user_id,
        {"kind": gesture.kind, "count": gesture.count},
        gesture.occurred_at,
        allow_text=gesture.kind != "chat_opened",
    )


async def emit_companion_reaction(user_id: int, user_message: str, assistant_bubbles: list[str]) -> None:
    occurred_at = utc_now()
    result = await _decide(
        user_id,
        {
            "kind": "dialogue",
            "user_message": user_message[-2000:],
            "assistant_response": "\n".join(assistant_bubbles)[-2000:],
        },
        occurred_at,
        allow_text=False,
    )
    if result.visual is not None:
        async with SESSION_LOCAL() as db:
            emit_ws_event(
                db,
                user_id=user_id,
                event_type="companion.reaction",
                payload={**result.visual.model_dump(mode="json"), "occurred_at": occurred_at.isoformat()},
            )
            await db.commit()
