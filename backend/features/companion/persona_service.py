from collections.abc import Collection, Mapping
from typing import Any

from infrastructure.constants import DEFAULT_LANGUAGE
from infrastructure.text import resolve_language, resolve_prompt_text
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import MemoryScope
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.appearance_ports import read_appearance_readiness
from features.companion.definition import parse_persona_definition
from features.companion.first_greeting import enqueue_first_greeting, schedule_first_greeting_claim
from features.companion.models import Persona
from features.companion.prompts import PERSONA_FIELD_LABELS, PERSONA_LABELS_TEXTS
from features.companion.query_contracts import PersonaSnapshot
from features.companion.repository import get_or_create_persona_row
from features.companion.schemas import OnboardingStateResponse
from features.conversation.bootstrap import ensure_main_conversation_for_user
from features.memory.memory_bootstrap import read_user_profile, record_user_profile
from features.memory.models import USER_PROFILE_MAX_CONTENT_CHARS
from features.memory.profile_fields import validate_profile_birthday

# 人设字段顺序属于对外契约的一部分，它决定渲染出的系统提示词片段形状
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "personality", "speaking_style")
_OPTIONAL_FIELDS: tuple[str, ...] = ("relationship", "biological_type", "gender")
_KNOWN_FIELDS: frozenset[str] = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_MAX_FIELD_LEN: int = 500
_EDITABLE_FIELDS = frozenset({"name", "personality", "relationship", "speaking_style"})

# 引导问答的原始字段，按提问顺序排列；未完成时以草稿存 definition_json，user_* 由 update_persona 路由进 Memory。
ONBOARDING_FIELDS: tuple[str, ...] = (
    "name",
    "biological_type",
    "gender",
    "relationship",
    "personality",
    "speaking_style",
    "voice",
    "user_call_name",
    "user_gender",
    "user_birthday",
    "user_hobbies",
    "user_freeform",
    "voice_setup_skipped",
)
_ONBOARDING_MAX_LEN: int = USER_PROFILE_MAX_CONTENT_CHARS

# voice 之前的字段构成角色阶段，由唯一的顺序事实源派生。
_VOICE_FIELD_INDEX: int = ONBOARDING_FIELDS.index("voice")
_CHARACTER_ONBOARDING_FIELDS: tuple[str, ...] = ONBOARDING_FIELDS[:_VOICE_FIELD_INDEX]


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
        if key in ONBOARDING_FIELDS
    }


def _validate_definition(definition: dict[str, Any]) -> dict[str, str]:
    if not isinstance(definition, dict):
        raise PersonaValidationError("persona definition must be an object")
    cleaned: dict[str, str] = {}
    for key, value in definition.items():
        if key not in _KNOWN_FIELDS:
            raise PersonaValidationError(f"unknown persona field: {key!r}", key)
        if not isinstance(value, str):
            raise PersonaValidationError(f"persona.{key} must be a string", key)
        stripped = value.strip()
        if not stripped:
            raise PersonaValidationError(f"persona.{key} must be non-empty", key)
        cleaned[key] = stripped[:_MAX_FIELD_LEN]
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
        if key not in ONBOARDING_FIELDS:
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
    if expected_fields is not None:
        if not persona.is_complete:
            raise PersonaValidationError("请先完成伙伴资料引导，再编辑伙伴资料。")
        submitted = set(definition)
        if not submitted or not submitted.issubset(_EDITABLE_FIELDS):
            raise PersonaValidationError("只能编辑名字、性格、关系和说话方式。")
        if not submitted.issubset(expected_fields):
            raise PersonaValidationError("expected_fields must contain the original value for each edited field")
        for key, original in expected_fields.items():
            if (current_draft.get(key) or None) != (original or None):
                raise PersonaConflictError("伙伴资料已在其他设备更新，请刷新后核对再保存。")
        persona_def = {key: value for key, value in current_draft.items() if key in _KNOWN_FIELDS}
        for key in submitted:
            value = definition[key]
            if key == "relationship" and isinstance(value, str) and not value.strip():
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
        avatar = await read_appearance_readiness(db, user_id)
        if avatar is not None and avatar.is_fullbody_confirmed:
            for locked in ("biological_type", "gender"):
                if locked in current_draft:
                    cleaned[locked] = current_draft[locked]
                else:
                    cleaned.pop(locked, None)
    for voice_field in ("voice", "voice_setup_skipped"):
        if current_draft.get(voice_field):
            cleaned[voice_field] = current_draft[voice_field]
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


def _state(answers: dict[str, str], next_field: str | None, complete: bool) -> OnboardingStateResponse:
    return OnboardingStateResponse(answers=answers, next_field=next_field, complete=complete)


async def _next_onboarding_step(db: AsyncSession, user_id: int, persona: Persona, draft: dict[str, str]) -> str | None:
    """返回必需的引导步骤；声音已选择或用户明确暂时使用文字后可完成。"""
    if not persona.is_complete:
        return next((f for f in _CHARACTER_ONBOARDING_FIELDS if not draft.get(f)), "portrait")
    if not persona.is_portrait_confirmed:
        return "portrait"
    avatar = await read_appearance_readiness(db, user_id)
    if avatar is None:
        return "portrait"
    if not avatar.seed_fullbody_url or not avatar.is_fullbody_confirmed:
        return "fullbody-reference"
    if not draft.get("voice") and draft.get("voice_setup_skipped") != "true":
        return "voice"
    return None


async def onboarded_user_ids(db: AsyncSession, user_ids: Collection[int]) -> set[int]:
    """只读筛选已完成必需引导的账户，与引导恢复使用同一完成条件。"""
    personas = await db.scalars(select(Persona).where(Persona.user_id.in_(user_ids), Persona.is_complete.is_(True)))
    return {
        persona.user_id
        for persona in personas
        if await _next_onboarding_step(db, persona.user_id, persona, load_persona_definition(persona)) is None
    }


async def get_onboarding_state(db: AsyncSession, user_id: int) -> OnboardingStateResponse:
    """从数据库恢复引导进度；可选个人资料不阻断完成。"""
    persona = await get_or_create_persona_row(db, user_id)
    draft = load_persona_definition(persona)
    next_step = await _next_onboarding_step(db, user_id, persona, draft)
    if next_step is None:
        return _state({}, None, True)
    if not persona.is_complete:
        return _state(draft, next_step, False)
    # 合并草稿与 Memory，让桌面端在形象与音色阶段仍能预填已答资料。
    user_profile = await read_user_profile(db, MemoryScope(user_id, "companion"))
    return _state({**draft, **user_profile}, next_step, False)


async def submit_onboarding_field(
    db: AsyncSession, user_id: int, field: str, value: str | None
) -> OnboardingStateResponse:
    """保存引导回答；人设定型后仅修改个人资料与声音选择。"""
    if field not in ONBOARDING_FIELDS:
        raise PersonaValidationError(f"unknown onboarding field: {field!r}", field)
    if field == "voice_setup_skipped" and value not in (None, "true"):
        raise PersonaValidationError("voice_setup_skipped must be true or null", field)
    if field.startswith("user_"):
        value = _onboarding_profile_value(field, value)
    if field == "user_birthday":
        _validate_birthday(value)
    persona = await get_or_create_persona_row(db, user_id)
    await db.refresh(persona, with_for_update=True)
    if persona.is_complete:
        if field.startswith("user_"):
            if value:
                await record_user_profile(
                    db,
                    MemoryScope(user_id, "companion"),
                    {field: value.strip()},
                )
                await db.commit()
            # 传空值不动 Memory 行：清除 user_* 条目通过记忆管理删除
            return _state(load_persona_definition(persona), None, True)
        # 声音选择和暂用文字都是明确操作，保存独立标记，不以伪造的音色名称占位。
        if field in ("voice", "voice_setup_skipped"):
            draft = load_persona_definition(persona)
            pending_step = await _next_onboarding_step(db, user_id, persona, draft)
            if value and value.strip():
                draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
                draft.pop("voice_setup_skipped" if field == "voice" else "voice", None)
            else:
                draft.pop(field, None)
            persona.definition_json = draft
            completed = pending_step == "voice" and bool(
                draft.get("voice") or draft.get("voice_setup_skipped") == "true",
            )
            if completed:
                await enqueue_first_greeting(db, user_id)
            await db.commit()
            if completed:
                schedule_first_greeting_claim(user_id)
            next_step = await _next_onboarding_step(db, user_id, persona, draft)
            return _state(draft, next_step, next_step is None)
        raise PersonaValidationError(
            f"onboarding field {field!r} cannot be edited after persona is finalized; use PUT /api/companion/persona",
            field,
        )
    draft = load_persona_definition(persona)
    if value and value.strip():
        draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
    else:
        draft.pop(field, None)
    persona.definition_json = draft
    await db.commit()
    answers = draft
    missing_character = next((f for f in _CHARACTER_ONBOARDING_FIELDS if not answers.get(f)), None)
    next_field = missing_character if missing_character is not None else "portrait"
    return _state(answers, next_field, False)
