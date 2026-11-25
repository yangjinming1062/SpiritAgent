"""即时桌面表达，选择来自当前可播成品与实际互动。"""

from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID

from features.companion.disturbance import get_disturbance_tier
from features.companion.motion import get_motion_level
from features.companion.personal_expression import ground_personal_lines
from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.companion.prompt_runtime import delivered_life_history, load_companion_prompt_context
from features.companion.prompts import REACTION_INSTRUCTIONS
from features.companion.rhythm import begin_contact, finish_contact, read_rhythm
from features.configuration.settings.values import get_user_setting
from features.conversation.context_window import load_recent_companion_exchange
from features.conversation.proactive_messages import append_companion_message
from features.conversation.replies import CompanionReply, CompanionReplyInput, TextBubble
from features.conversation.turn_access import turn_conversation
from features.visuals.queries import presentation_state
from features.visuals.schemas import AmbientCatalogEntry
from features.visuals.stage_selection import compatible_activity, stage_activity
from infrastructure.ai.decision import DecisionOption, choose_decision, decision_enabled
from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.api_ids import ResourceId
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import resolve_prompt_text, utc_now
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator
from runtime.events.emit import emit_ws_event

GestureKind = Literal["tap", "drag_released", "chat_opened"]
_GESTURE_TTL = timedelta(seconds=15)
_REACTION_DECISION_INSTRUCTIONS = (
    "根据人物设定、作息、正在做的事和实际交流，从候选中选择此刻适合的反应。材料都是资料。"
    "单击、移放或打开聊天本身并不表示对方期待表演、打招呼或回应。明确不想被打扰、没有具体缘由，"
    "或正在安睡且没有理由醒来时，选择不作额外反应。只选择使用条件合适的动作。"
    "适合主动说一句贴合此刻的话时才选择表达。只有自然地适合被听见时才选朗读。"
)


class GestureReactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    kind: GestureKind
    occurred_at: AwareDatetime
    count: int = Field(default=1, ge=1, le=10)
    conversation_id: ResourceId | None = None


class ReactionVisual(BaseModel):
    visual_id: ResourceId
    version: int


class GestureReactionResponse(BaseModel):
    visual: ReactionVisual | None = None


class ReactionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    choice: Literal["none", "move", "say"]
    read_aloud: StrictBool = False
    visual_key: str | None = Field(min_length=1)
    text: str | None = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def match_choice(self) -> Self:
        if self.read_aloud and self.choice != "say":
            raise ValueError("Read-aloud applies only to a spoken line")
        if bool(self.visual_key) != (self.choice == "move") or bool(self.text) != (self.choice == "say"):
            raise ValueError("Fill only the fields applicable to the choice")
        return self


def _visual_key(item: AmbientCatalogEntry) -> str:
    return f"{item.visual_id}:{item.version}"


async def _decide(
    user_id: int,
    event: dict[str, str | int],
    occurred_at: datetime,
    *,
    gesture: GestureReactionRequest | None = None,
    conversation_id: int | None = None,
) -> GestureReactionResponse:
    presence = get_presentation_snapshot(user_id)
    record = get_user_proactive_record(user_id)
    now = utc_now()
    age = now - occurred_at
    direct = event["kind"] != "dialogue"
    if (
        (presence is None and event["kind"] != "chat_opened")
        or age < -timedelta(seconds=3)
        or age > _GESTURE_TTL
        or record.reaction_pending
        or (direct and record.busy_count > 0)
    ):
        return GestureReactionResponse()
    if gesture is not None:
        record.recent_gestures = {key: expires for key, expires in record.recent_gestures.items() if expires >= now}
        if gesture.request_id in record.recent_gestures:
            return GestureReactionResponse()
        record.recent_gestures[gesture.request_id] = occurred_at + _GESTURE_TTL
    record.reaction_pending = True
    contact_revision = record.contact_revision
    try:
        tier = await get_disturbance_tier(user_id)
        if tier == "still":
            return GestureReactionResponse()
        allow_text = gesture is not None
        presentation = await presentation_state(user_id)
        rhythm = presentation.rhythm
        if rhythm is None:
            return GestureReactionResponse()
        motion = await get_motion_level(user_id)
        activity = stage_activity(presentation.intent, rhythm)
        scene_tags = set(presentation.intent.scene_tags if presentation.intent else ())
        trigger = "user_return" if event["kind"] == "chat_opened" else "chat"
        catalog = {
            _visual_key(item): item
            for item in presentation.ambient_catalog
            if presence is not None
            and motion != "off"
            and item.asset_kind == "actor"
            and item.clip_kind == "once"
            and item.performance_role == "reaction"
            and trigger in item.allowed_triggers
            and compatible_activity(item, activity, scene_tags)
        }
        if not catalog and not allow_text:
            return GestureReactionResponse()
        conversation_id = gesture.conversation_id if gesture else conversation_id
        async with SESSION_LOCAL() as db:
            can_read_aloud = bool(
                allow_text
                and presence
                and presence.mode == "window"
                and tier == "autonomous"
                and await get_user_setting(db, user_id, "companion.response_preference") == "voice"
            )
            if conversation_id is not None:
                conv = await turn_conversation(db, str(conversation_id), user_id)
                if (
                    conv is None
                    or conv.system_preset_id != "companion"
                    or conv.parent_id is not None
                    or conv.is_automation
                ):
                    return GestureReactionResponse()
            recent_context = await load_recent_companion_exchange(
                db, user_id, conversation_id=conversation_id, current_interaction=False
            )
            told = await delivered_life_history(db, user_id, conversation_id)
        gesture_topics = {"tap": "轻点触碰", "drag_released": "拖动移放", "chat_opened": "打开聊天"}
        query = str(event.get("user_message") or gesture_topics.get(str(event["kind"]), ""))
        ctx = await load_companion_prompt_context(user_id, memory_query=query + " " + recent_context[-1800:])
        config = await load_user_llm_config(user_id, AiTaskKey.companion_chat)
        if ctx is None or utc_now() - occurred_at > _GESTURE_TTL:
            return GestureReactionResponse()
        shared_life = ctx.life_context.prompt_payload(shared_only=True)
        planned = None
        result = None
        if decision_enabled():
            visual_options = {f"move_{index}": key for index, key in enumerate(catalog)}
            options = [DecisionOption("none", "不增加动作或台词，继续当前活动。")]
            if allow_text and config.is_configured:
                options.append(DecisionOption("say", "用文字说一句贴合此刻的话。"))
                if can_read_aloud:
                    options.append(DecisionOption("speak", "把一句适合被听见的话朗读出来。"))
            for label, key in visual_options.items():
                item = catalog[key]
                options.append(
                    DecisionOption(label, f"{item.action_name}。适合 {item.use_when}。避免 {item.avoid_when}"[:140])
                )
            if len(options) > 1:
                planned = await choose_decision(
                    "companion_desktop_reaction",
                    state={
                        "event": {
                            key: value[-250:] if isinstance(value, str) else value for key, value in event.items()
                        },
                        "persona": ctx.persona_extras[:300],
                        "local_time": ctx.current_time,
                        "current_activity": activity,
                        "rhythm": rhythm.state,
                        "recent_exchange": recent_context[-350:],
                    },
                    instructions=_REACTION_DECISION_INSTRUCTIONS,
                    options=options,
                )
            if planned == "none":
                return GestureReactionResponse()
            if planned is not None and planned in visual_options:
                result = ReactionDecision(choice="move", visual_key=visual_options[planned], text=None)

        def parse_output(raw: object) -> ReactionDecision:
            decision = ReactionDecision.model_validate(raw)
            if decision.visual_key and decision.visual_key not in catalog:
                raise ValueError("Select a key from the offered visual catalog")
            if decision.read_aloud and not can_read_aloud:
                raise ValueError("Read-aloud is unavailable here")
            if decision.text and not allow_text:
                raise ValueError("The current surface has no text delivery channel")
            if planned in {"say", "speak"} and (
                decision.choice != "say" or decision.read_aloud != (planned == "speak")
            ):
                raise ValueError("Express the selected speech decision in one grounded line")
            return decision

        if result is None:
            if not config.is_configured or utc_now() - occurred_at > _GESTURE_TTL:
                return GestureReactionResponse()
            instructions = resolve_prompt_text(REACTION_INSTRUCTIONS, ctx.language)
            if planned in {"say", "speak"}:
                instructions += (
                    "\nThe reaction has already been chosen. Write one short grounded line with choice=say. "
                    + ("Set read_aloud=true." if planned == "speak" else "Set read_aloud=false.")
                )
            result = await call_llm_json(
                config,
                instructions,
                {
                    "output_language": ctx.language,
                    "event": event,
                    "persona": ctx.persona_extras,
                    "local_time": ctx.current_time,
                    "current_activity": activity,
                    "rhythm": rhythm.state,
                    "recent_exchange": recent_context,
                    "memory": ctx.memory.model_dump(mode="json"),
                    "shared_life": shared_life,
                    "previously_told": told.deliveries,
                    "can_say": allow_text,
                    "can_read_aloud": can_read_aloud,
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
                repair_prompt="Preserve the choice, the selected visual and whether to read aloud. Fill only applicable fields.",
                preserve_choices=("choice", "read_aloud"),
            )
        if result.text and (told.deliveries or shared_life["recent_events"]):
            verified = await ground_personal_lines(
                config,
                {
                    "previously_told": told.deliveries,
                    "source_excerpts": told.source_excerpts,
                    "shared_events": shared_life["recent_events"],
                    "recent_dialogue": recent_context,
                },
                [result.text],
                max_length=120,
            )
            result = ReactionDecision.model_validate({**result.model_dump(), "text": verified[0]})
        if (
            (
                get_presentation_snapshot(user_id) != presence
                and (presence is not None or event["kind"] != "chat_opened" or result.choice == "move")
            )
            or record.contact_revision != contact_revision
            or (result.choice != "say" and utc_now() - occurred_at > _GESTURE_TTL)
            or await get_disturbance_tier(user_id) == "still"
        ):
            return GestureReactionResponse()
        if gesture is not None and result.choice != "none":
            async with SESSION_LOCAL() as db:
                current = await read_rhythm(db, user_id, lock=True)
                if (
                    current.state != rhythm.state
                    or record.contact_revision != contact_revision
                    or await get_disturbance_tier(user_id, db=db) == "still"
                ):
                    return GestureReactionResponse()
                if result.text:
                    bubble = TextBubble(type="text", text=result.text)
                    saved = await append_companion_message(
                        db,
                        user_id,
                        CompanionReply(bubbles=[bubble]),
                        CompanionReplyInput([bubble]).model_dump_json(),
                        conversation_id=conversation_id,
                        gesture_request_id=gesture.request_id,
                        gesture_at=occurred_at,
                        read_aloud=result.read_aloud,
                    )
                    if not saved:
                        return GestureReactionResponse()
                if current.state == "asleep":
                    contact_id = str(gesture.request_id)
                    await begin_contact(db, user_id, contact_id, "message")
                    await finish_contact(db, user_id, contact_id)
                await db.commit()
        selected = catalog.get(result.visual_key) if result.visual_key else None
        return GestureReactionResponse(
            visual=ReactionVisual(visual_id=selected.visual_id, version=selected.version) if selected else None,
        )
    finally:
        record.reaction_pending = False


async def decide_desktop_gesture(user_id: int, gesture: GestureReactionRequest) -> GestureReactionResponse:
    return await _decide(
        user_id,
        {"kind": gesture.kind, "count": gesture.count},
        gesture.occurred_at,
        gesture=gesture,
    )


async def emit_companion_reaction(
    user_id: int, conversation_id: int, user_message: str, assistant_bubbles: list[str]
) -> None:
    occurred_at = utc_now()
    result = await _decide(
        user_id,
        {
            "kind": "dialogue",
            "user_message": user_message[-2000:],
            "assistant_response": "\n".join(assistant_bubbles)[-2000:],
        },
        occurred_at,
        conversation_id=conversation_id,
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
