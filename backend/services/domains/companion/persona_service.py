import json
import re
from datetime import date
from typing import Any

from components import DEFAULT_LANGUAGE, resolve_language, resolve_prompt_text
from modules.companion import (
    AvatarAsset,
    CharacterCardSnapshot,
    OnboardingStateResponse,
    Persona,
    parse_persona_definition,
)
from modules.memory import USER_PROFILE_MAX_CONTENT_CHARS
from prompts.companion import PERSONA_FIELD_LABELS, PERSONA_LABELS_TEXTS
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope
from services.domains.conversation import ensure_system_conversations_for_user
from services.domains.memory import read_user_profile, record_user_profile

from .character_card import render_character_appearance
from .first_greeting import enqueue_first_greeting, schedule_first_greeting_claim

# 人设字段顺序属于对外契约的一部分，它决定渲染出的系统提示词片段形状
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "personality", "speaking_style")
_OPTIONAL_FIELDS: tuple[str, ...] = ("relationship", "biological_type", "gender")
_KNOWN_FIELDS: frozenset[str] = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_MAX_FIELD_LEN: int = 500

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


# user_birthday 统一为 YYYY-MM-DD 真实日期；两个写入口各自校验，不信任前端。
_BIRTHDAY_PATTERN: re.Pattern[str] = re.compile(r"\d{4}-\d{2}-\d{2}")


def _validate_birthday(value: str | None) -> None:
    """空白视为未填写，直接通过。"""
    stripped = (value or "").strip()
    if not stripped:
        return
    if not _BIRTHDAY_PATTERN.fullmatch(stripped):
        raise PersonaValidationError("persona.user_birthday must be formatted as YYYY-MM-DD", "user_birthday")
    try:
        date.fromisoformat(stripped)
    except ValueError as exc:
        raise PersonaValidationError(
            f"persona.user_birthday is not a real date: {stripped!r}",
            "user_birthday",
        ) from exc


def load_persona_definition(persona: Persona | None) -> dict[str, str]:
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
        profile[key] = (value or "").strip()[:_ONBOARDING_MAX_LEN]
    return profile


async def get_or_create_persona(db: AsyncSession, user_id: int) -> Persona:
    """查询人设，不存在则插入一行；并发首次创建由唯一约束收敛到同一行。刻意不 commit，以便调用方把 user_profile 与 persona 放在同一事务里写。"""
    persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
    if persona is None:
        await db.execute(
            insert(Persona)
            .values(user_id=user_id, definition_json="{}")
            .on_conflict_do_nothing(index_elements=[Persona.user_id]),
        )
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one()
    return persona


async def update_persona(db: AsyncSession, user_id: int, definition: dict[str, Any]) -> Persona:
    if not isinstance(definition, dict):
        raise PersonaValidationError("persona definition must be an object")
    user_profile = _validate_user_profile(definition)
    _validate_birthday(user_profile.get("user_birthday"))
    persona_def = {k: v for k, v in definition.items() if not k.startswith("user_")}
    cleaned = _validate_definition(persona_def)

    await record_user_profile(db, MemoryScope(user_id, "companion"), user_profile)
    persona = await get_or_create_persona(db, user_id)
    await db.refresh(persona, with_for_update=True)
    current_draft = load_persona_definition(persona)
    if current_draft.get("voice"):
        cleaned["voice"] = current_draft["voice"]
    # 锁定字段只保留已有值；原本缺失的字段也不能通过 PUT 补入。
    sealed = await db.scalar(
        select(AvatarAsset.is_fullbody_confirmed).where(
            AvatarAsset.user_id == user_id,
            AvatarAsset.active.is_(True),
        ),
    )
    if sealed:
        for locked in ("biological_type", "gender"):
            if locked in current_draft:
                cleaned[locked] = current_draft[locked]
            else:
                cleaned.pop(locked, None)
    persona.definition_json = json.dumps(cleaned, ensure_ascii=False)
    # persona_extras 不缓存：build_system_prompt_extras 运行期按 session language 从 definition_json 实时渲染，避免英语会话拿到 onboarding 烤进去的中文头部。
    persona.is_complete = True
    await db.commit()
    # 每次保存人设都幂等补齐 SYSTEM_PRESET_CATALOG 中缺失的系统预设对话。
    await ensure_system_conversations_for_user(db, persona.user_id)
    return persona


async def confirm_portrait(db: AsyncSession, user_id: int) -> None:
    persona = await get_or_create_persona(db, user_id)
    persona.is_portrait_confirmed = True
    persona.portrait_confirmed_at = func.now()
    await db.commit()


def build_system_prompt_extras(
    persona: Persona | None,
    *,
    language: str = DEFAULT_LANGUAGE,
    character: CharacterCardSnapshot | None = None,
) -> str:
    """从 persona.definition_json 按当前 session 语言实时渲染角色设定块。"""
    if persona is None or not persona.is_complete:
        return ""
    definition = load_persona_definition(persona)
    if not definition:
        return ""
    return (
        render_extras(definition, language=language) + "\n" + render_character_appearance(character, language=language)
    )


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
    """返回下一项必需的引导步骤；None 表示角色、头像、全身种子确认与音色均已完成。"""
    if not persona.is_complete:
        return next((f for f in _CHARACTER_ONBOARDING_FIELDS if not draft.get(f)), "portrait")
    if not persona.is_portrait_confirmed:
        return "portrait"
    avatar = (
        await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
    ).scalar_one_or_none()
    if avatar is None:
        return "portrait"
    if not avatar.seed_fullbody_url or not avatar.is_fullbody_confirmed:
        return "fullbody-reference"
    if not draft.get("voice"):
        return "voice"
    return None


async def get_onboarding_state(db: AsyncSession, user_id: int) -> OnboardingStateResponse:
    """从数据库恢复引导进度；complete 以角色、头像、全身种子确认与音色为门槛。"""
    persona = await get_or_create_persona(db, user_id)
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
    db: AsyncSession,
    user_id: int,
    field: str,
    value: str | None,
    *,
    expected_version: int | None = None,
) -> OnboardingStateResponse:
    """写入一条引导回答；is_complete 之后仅 user_*/voice 可改，角色字段须走 PUT /persona。"""
    if field not in ONBOARDING_FIELDS:
        raise PersonaValidationError(f"unknown onboarding field: {field!r}", field)
    if expected_version is not None and (
        type(expected_version) is not int or expected_version < 0 or not field.startswith("user_")
    ):
        raise PersonaValidationError("expected_version must be a non-negative int for user profile fields", field)
    if field == "user_birthday":
        _validate_birthday(value)
    persona = await get_or_create_persona(db, user_id)
    if persona.is_complete:
        if field.startswith("user_"):
            if value and value.strip():
                try:
                    await record_user_profile(
                        db,
                        MemoryScope(user_id, "companion"),
                        {field: value.strip()[:_ONBOARDING_MAX_LEN]},
                        expected_versions={field: expected_version} if expected_version is not None else None,
                    )
                except ValueError as exc:
                    raise PersonaValidationError(str(exc), field) from exc
                await db.commit()
            # 传空值不动 Memory 行：清除 user_* 条目通过记忆管理删除
            return _state(load_persona_definition(persona), None, True)
        # voice 不是人设字段，故此处只动草稿
        if field == "voice":
            draft = load_persona_definition(persona)
            pending_step = await _next_onboarding_step(db, user_id, persona, draft)
            if value and value.strip():
                draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
            else:
                draft.pop(field, None)
            persona.definition_json = json.dumps(draft, ensure_ascii=False)
            # 音色是最后一项必需资料：只在本次写入使引导由未完成变为完成时才同事务保存初次问候意图，已完成后的修改不触发。
            completed = pending_step == "voice" and bool(draft.get("voice"))
            if completed:
                await enqueue_first_greeting(db, user_id)
            await db.commit()
            if completed:
                schedule_first_greeting_claim(user_id)
            return _state(draft, None, True)
        raise PersonaValidationError(
            f"onboarding field {field!r} cannot be edited after persona is finalized; use PUT /api/companion/persona",
            field,
        )
    draft = load_persona_definition(persona)
    if value and value.strip():
        draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
    else:
        draft.pop(field, None)
    persona.definition_json = json.dumps(draft, ensure_ascii=False)
    await db.commit()
    answers = draft
    missing_character = next((f for f in _CHARACTER_ONBOARDING_FIELDS if not answers.get(f)), None)
    next_field = missing_character if missing_character is not None else "portrait"
    return _state(answers, next_field, False)
