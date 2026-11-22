from collections.abc import Collection, Mapping
from typing import Any

from infrastructure.constants import DEFAULT_LANGUAGE
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language, resolve_prompt_text
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import MemoryScope
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.appearance_ports import read_appearance_readiness
from features.companion.definition import ONBOARDING_PROFILE_STEP, parse_persona_definition
from features.companion.first_greeting import enqueue_first_greeting, schedule_first_greeting_claim
from features.companion.models import Persona
from features.companion.prompts import PERSONA_FIELD_LABELS, PERSONA_LABELS_TEXTS
from features.companion.query_contracts import PersonaSnapshot
from features.companion.repository import get_or_create_persona_row
from features.companion.scene_ports import ensure_initial_scene
from features.companion.schemas import OnboardingProfileRequest, OnboardingStateResponse
from features.conversation.bootstrap import ensure_main_conversation_for_user
from features.memory.contracts import MemoryConflictError
from features.memory.memory_admin import apply_user_profile
from features.memory.memory_bootstrap import read_user_profile, record_user_profile
from features.memory.models import USER_PROFILE_MAX_CONTENT_CHARS
from features.memory.profile_fields import USER_PROFILE_FIELDS, validate_profile_birthday

logger = get_logger(__name__)

# 人设字段顺序属于对外契约的一部分，它决定渲染出的系统提示词片段形状
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "biological_type", "speaking_style")
_OPTIONAL_FIELDS: tuple[str, ...] = ("personality", "relationship", "gender", "age", "occupation", "background")
_KNOWN_FIELDS: frozenset[str] = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_MAX_FIELD_LEN: int = 500
_EDITABLE_FIELDS = frozenset(
    {"name", "personality", "relationship", "speaking_style", "age", "occupation", "background"}
)

_ONBOARDING_FIELDS = _KNOWN_FIELDS | {
    "voice",
    "voice_setup_skipped",
    "environment_skipped",
}
_ONBOARDING_MAX_LEN: int = USER_PROFILE_MAX_CONTENT_CHARS


class PersonaValidationError(ValueError):
    """人设校验失败；field 为出错字段名，结构性错误时为 None。"""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class PersonaConflictError(ValueError):
    pass


def _validate_birthday(value: str | None) -> None:
    """空白视为未填写，直接通过。"""
    try:
        validate_profile_birthday(value)
    except ValueError as exc:
        raise PersonaValidationError(str(exc), "user_birthday") from exc


def load_persona_definition(persona: Persona | PersonaSnapshot | None) -> dict[str, str]:
    if persona is None:
        return {}
    return {
        key: value
        for key, value in parse_persona_definition(persona.definition_json).items()
        if key in _ONBOARDING_FIELDS
    }


def _validate_definition(definition: dict[str, Any]) -> dict[str, str]:
    if not isinstance(definition, dict):
        raise PersonaValidationError("persona definition must be an object")
    cleaned: dict[str, str] = {}
    for key, value in definition.items():
        if key not in _KNOWN_FIELDS:
            raise PersonaValidationError(f"unknown persona field: {key!r}", key)
        if key in _OPTIONAL_FIELDS and (value is None or (isinstance(value, str) and not value.strip())):
            continue
        if not isinstance(value, str):
            raise PersonaValidationError(f"persona.{key} must be a string", key)
        stripped = value.strip()
        if not stripped:
            raise PersonaValidationError(f"persona.{key} must be non-empty", key)
        if "\x00" in stripped:
            raise PersonaValidationError(f"persona.{key} must not contain NUL characters", key)
        maximum = 2000 if key == "background" else _MAX_FIELD_LEN
        if len(stripped) > maximum:
            raise PersonaValidationError(f"persona.{key} must be at most {maximum} characters", key)
        cleaned[key] = stripped
    for key in _REQUIRED_FIELDS:
        if key not in cleaned:
            raise PersonaValidationError(f"persona.{key} is required", key)
    return cleaned


def _onboarding_profile_value(field: str, value: str | None) -> str:
    if value is not None and "\x00" in value:
        raise PersonaValidationError(f"persona.{field} must not contain NUL characters", field)
    return (value or "").strip()[:_ONBOARDING_MAX_LEN]


def _validate_user_profile(definition: dict[str, Any]) -> dict[str, str]:
    """取出 user_* 回答：None 视为未填写，其余须为字符串；与引导问答一致，去空白后截断到 _ONBOARDING_MAX_LEN。"""
    profile: dict[str, str] = {}
    for key, value in definition.items():
        if not key.startswith("user_"):
            continue
        if key not in USER_PROFILE_FIELDS:
            raise PersonaValidationError("unknown user profile field", key)
        if value is not None and not isinstance(value, str):
            raise PersonaValidationError(f"persona.{key} must be a string", key)
        profile[key] = _onboarding_profile_value(key, value)
    return profile


async def update_persona(
    db: AsyncSession,
    user_id: int,
    definition: dict[str, Any],
    *,
    expected_fields: Mapping[str, str | None] | None = None,
) -> Persona:
    if not isinstance(definition, dict):
        raise PersonaValidationError("persona definition must be an object")
    persona = await get_or_create_persona_row(db, user_id)
    await db.refresh(persona, with_for_update=True)
    current_draft = load_persona_definition(persona)
    if persona.is_complete and expected_fields is None:
        raise PersonaConflictError("人物设定已保存，请核对最新内容后再提交修改。")
    if expected_fields is not None:
        if not persona.is_complete:
            raise PersonaValidationError("请先完成伙伴资料引导，再编辑伙伴资料。")
        submitted = set(definition)
        if not submitted or not submitted.issubset(_KNOWN_FIELDS):
            raise PersonaValidationError("只能编辑人物设定。")
        if submitted - _EDITABLE_FIELDS:
            avatar = await read_appearance_readiness(db, user_id)
            if avatar is not None and avatar.is_fullbody_confirmed:
                raise PersonaValidationError("已确认的物种和性别不可修改。")
        if not submitted.issubset(expected_fields):
            raise PersonaValidationError("expected_fields must contain the original value for each edited field")
        for key, original in expected_fields.items():
            if (current_draft.get(key) or None) != (original or None):
                raise PersonaConflictError("伙伴资料已在其他设备更新，请刷新后核对再保存。")
        persona_def = {key: value for key, value in current_draft.items() if key in _KNOWN_FIELDS}
        for key in submitted:
            value = definition[key]
            if key in _OPTIONAL_FIELDS and (value is None or isinstance(value, str) and not value.strip()):
                persona_def.pop(key, None)
            else:
                persona_def[key] = value
        cleaned = _validate_definition(persona_def)
    else:
        user_profile = _validate_user_profile(definition)
        _validate_birthday(user_profile.get("user_birthday"))
        persona_def = {key: value for key, value in definition.items() if not key.startswith("user_")}
        cleaned = _validate_definition(persona_def)
        await record_user_profile(db, MemoryScope(user_id, "companion"), user_profile)
    for voice_field in ("voice", "voice_setup_skipped", "environment_skipped"):
        if current_draft.get(voice_field):
            cleaned[voice_field] = current_draft[voice_field]
    if ONBOARDING_PROFILE_STEP in persona.definition_json:
        cleaned[ONBOARDING_PROFILE_STEP] = persona.definition_json[ONBOARDING_PROFILE_STEP]
    persona.definition_json = cleaned
    # persona_extras 不缓存：build_system_prompt_extras 运行期按 session language 从 definition_json 实时渲染，避免英语会话拿到 onboarding 烤进去的中文头部。
    persona.is_complete = True
    session_id = await ensure_main_conversation_for_user(db, persona.user_id, commit=False)
    if cleaned.get("name") != current_draft.get("name"):
        emit_ws_event(db, user_id=user_id, event_type="session.list_changed", payload={"session_id": session_id})
    await db.commit()
    return persona


async def confirm_portrait(db: AsyncSession, user_id: int) -> None:
    persona = await get_or_create_persona_row(db, user_id)
    persona.is_portrait_confirmed = True
    persona.portrait_confirmed_at = func.now()
    await db.commit()


def build_system_prompt_extras(persona: Persona | None, *, language: str = DEFAULT_LANGUAGE) -> str:
    """从 persona.definition_json 按当前 session 语言实时渲染角色设定块。"""
    if persona is None or not persona.is_complete:
        return ""
    definition = load_persona_definition(persona)
    if not definition:
        return ""
    return render_extras(definition, language=language)


def render_extras(definition: dict[str, str], *, language: str = DEFAULT_LANGUAGE) -> str:
    lines = [resolve_prompt_text(PERSONA_LABELS_TEXTS, language)]
    labels = PERSONA_FIELD_LABELS[resolve_language(language)]
    for key in _REQUIRED_FIELDS + _OPTIONAL_FIELDS:
        if key in definition:
            lines.append(f"- **{labels[key]}**: {definition[key]}")
    return "\n".join(lines)


def _state(persona: Persona, answers: dict[str, str], next_field: str | None) -> OnboardingStateResponse:
    step = persona.definition_json.get(ONBOARDING_PROFILE_STEP)
    return OnboardingStateResponse(
        answers=answers,
        next_field=next_field,
        complete=next_field is None,
        persona_complete=persona.is_complete,
        profile_outcome=step if step in ("saved", "skipped") else None,
    )


async def _next_onboarding_step(db: AsyncSession, user_id: int, persona: Persona, draft: dict[str, str]) -> str | None:
    """桌面恢复和生活调度共用同一完成条件。旧引导没有资料阶段标记。"""
    if not persona.is_complete:
        return _next_character_step(draft)
    if not persona.is_portrait_confirmed:
        return "portrait"
    avatar = await read_appearance_readiness(db, user_id)
    if avatar is None:
        return "portrait"
    if not avatar.seed_fullbody_url or not avatar.is_fullbody_confirmed:
        return "fullbody-reference"
    if not draft.get("voice") and draft.get("voice_setup_skipped") != "true":
        return "voice"
    profile_step = persona.definition_json.get(ONBOARDING_PROFILE_STEP)
    if profile_step in ("environment", "user_profile"):
        return profile_step
    return None


def _next_character_step(draft: dict[str, str]) -> str:
    for field in _REQUIRED_FIELDS:
        if not draft.get(field):
            return field
    return "background"


async def onboarded_user_ids(db: AsyncSession, user_ids: Collection[int]) -> set[int]:
    """只读筛选已完成必需引导的账户，与引导恢复使用同一完成条件。"""
    personas = await db.scalars(select(Persona).where(Persona.user_id.in_(user_ids), Persona.is_complete.is_(True)))
    return {
        persona.user_id
        for persona in personas
        if await _next_onboarding_step(db, persona.user_id, persona, load_persona_definition(persona)) is None
    }


async def get_onboarding_state(db: AsyncSession, user_id: int) -> OnboardingStateResponse:
    """从已保存的草稿及已确认素材恢复建档进度。"""
    persona = await get_or_create_persona_row(db, user_id)
    draft = load_persona_definition(persona)
    next_step = await _next_onboarding_step(db, user_id, persona, draft)
    if next_step is None:
        return _state(persona, {}, None)
    return _state(persona, draft, next_step)


async def submit_onboarding_field(
    db: AsyncSession, user_id: int, field: str, value: str | None, *, include_profile: bool = False
) -> OnboardingStateResponse:
    """保存建档草稿、声音选择和默认环境决定。"""
    if field not in _ONBOARDING_FIELDS and field != "environment_complete":
        raise PersonaValidationError(f"unknown onboarding field: {field!r}", field)
    if include_profile and field not in ("voice", "voice_setup_skipped"):
        raise PersonaValidationError("资料步骤须在确认声音时登记。", field)
    if field == "environment_complete" and value != "true":
        raise PersonaValidationError("environment_complete must be true", field)
    if field in ("voice_setup_skipped", "environment_skipped") and value not in (None, "true"):
        raise PersonaValidationError(f"{field} must be true or null", field)
    persona = await get_or_create_persona_row(db, user_id)
    await db.refresh(persona, with_for_update=True)
    if persona.is_complete:
        # 声音选择和暂用文字都是明确操作，保存独立标记，不以伪造的音色名称占位。
        if field in ("voice", "voice_setup_skipped"):
            draft = load_persona_definition(persona)
            pending_step = await _next_onboarding_step(db, user_id, persona, draft)
            if include_profile and pending_step not in ("voice", "environment", "user_profile", None):
                raise PersonaValidationError("请先确认伙伴形象。", field)
            if value and value.strip():
                draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
                draft.pop("voice_setup_skipped" if field == "voice" else "voice", None)
            else:
                draft.pop(field, None)
            step = persona.definition_json.get(ONBOARDING_PROFILE_STEP)
            voice_confirmed = bool(draft.get("voice") or draft.get("voice_setup_skipped") == "true")
            if include_profile and voice_confirmed and pending_step == "voice" and step is None:
                step = "environment"
            if step:
                draft[ONBOARDING_PROFILE_STEP] = step
            persona.definition_json = draft
            completed = pending_step == "voice" and voice_confirmed and step is None
            if completed:
                await enqueue_first_greeting(db, user_id)
            await db.commit()
            if completed:
                schedule_first_greeting_claim(user_id)
            next_step = await _next_onboarding_step(db, user_id, persona, draft)
            return _state(persona, load_persona_definition(persona), next_step)
        # 跳过环境步骤即用户决定使用默认初始场景；标记先落库，默认场景创建失败不阻断引导，由启动恢复重试。
        if field in ("environment_skipped", "environment_complete"):
            draft = dict(persona.definition_json)
            step = draft.get(ONBOARDING_PROFILE_STEP)
            if step in ("saved", "skipped"):
                return await get_onboarding_state(db, user_id)
            if field == "environment_complete" and step is None:
                raise PersonaValidationError("请先完成声音步骤。", field)
            use_default = field == "environment_skipped" and value == "true"
            schedule_default = use_default and (step is None or draft.get("environment_skipped") != "true")
            if field == "environment_skipped":
                if use_default:
                    draft[field] = "true"
                else:
                    draft.pop(field, None)
            if step == "environment" and value == "true":
                draft[ONBOARDING_PROFILE_STEP] = "user_profile"
            persona.definition_json = draft
            await db.commit()
            if schedule_default:
                try:
                    await ensure_initial_scene(user_id)
                except Exception:
                    logger.warning("default initial scene not scheduled", extra={"user_id": user_id}, exc_info=True)
            next_step = await _next_onboarding_step(db, user_id, persona, draft)
            return _state(persona, load_persona_definition(persona), next_step)
        raise PersonaValidationError(
            f"onboarding field {field!r} cannot be edited after persona is finalized; use PUT /api/companion/persona",
            field,
        )
    draft = load_persona_definition(persona)
    if value is not None and "\x00" in value:
        raise PersonaValidationError(f"persona.{field} must not contain NUL characters", field)
    if field not in _KNOWN_FIELDS:
        raise PersonaValidationError("请先保存人物设定。", field)
    maximum = 2000 if field == "background" else _MAX_FIELD_LEN
    if value and len(value.strip()) > maximum:
        raise PersonaValidationError(f"persona.{field} must be at most {maximum} characters", field)
    if value and value.strip():
        draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
    else:
        draft.pop(field, None)
    persona.definition_json = draft
    await db.commit()
    return _state(persona, draft, _next_character_step(draft))


async def complete_onboarding_profile(
    db: AsyncSession, user_id: int, request: OnboardingProfileRequest
) -> OnboardingStateResponse:
    if request.decision == "skip" and request.changes:
        raise PersonaValidationError("跳过资料时不能提交修改。")
    persona = await get_or_create_persona_row(db, user_id)
    await db.refresh(persona, with_for_update=True)
    draft = load_persona_definition(persona)
    next_step = await _next_onboarding_step(db, user_id, persona, draft)
    scope = MemoryScope(user_id, "companion")
    if next_step is None:
        # 回执丢失时核对真实结果。另一个窗口的收尾不能覆盖后来在资料页做的修改。
        if request.decision == "save":
            saved = await read_user_profile(db, scope)
            if persona.definition_json.get(ONBOARDING_PROFILE_STEP) != "saved" or any(
                saved.get(change.field, "") != (change.value or "").strip() for change in request.changes
            ):
                raise MemoryConflictError("引导已完成，请在「我的资料」核对并维护最新内容。")
        return _state(persona, {}, None)
    if next_step != "user_profile":
        raise PersonaConflictError("请先完成之前的引导步骤。")
    if request.decision == "save":
        rows = await apply_user_profile(db, scope, request.changes, source="onboarding")
        if not any(row.slot_key in USER_PROFILE_FIELDS and row.content for row in rows):
            raise PersonaValidationError("请填写至少一项资料，或选择暂时跳过。")
    persona.definition_json = {
        **persona.definition_json,
        ONBOARDING_PROFILE_STEP: "saved" if request.decision == "save" else "skipped",
    }
    await enqueue_first_greeting(db, user_id)
    await db.commit()
    schedule_first_greeting_claim(user_id)
    return _state(persona, {}, None)
