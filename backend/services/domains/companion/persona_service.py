import json
from typing import Any

from components import DEFAULT_LANGUAGE, get_logger, resolve_prompt_text, safe_json_loads
from modules.companion import AvatarAsset, Persona
from modules.ws import emit_ws_event
from prompts.companion import PERSONA_LABELS_TEXTS
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope
from services.domains.conversation import ensure_system_conversations_for_user
from services.domains.memory import extract_user_profile, read_user_profile, record_user_profile

logger = get_logger(__name__)

# 人设字段顺序属于对外契约的一部分，它决定渲染出的系统提示词片段形状
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "personality", "speaking_style")
_OPTIONAL_FIELDS: tuple[str, ...] = ("appearance", "relationship", "biological_type", "gender")
_KNOWN_FIELDS: frozenset[str] = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_MAX_FIELD_LEN: int = 500

# 引导问答的原始字段，按提问顺序排列；未完成时以草稿形式存在 definition_json 中，user_* 由 update_persona 路由进 Memory。
ONBOARDING_FIELDS: tuple[str, ...] = (
    "name",
    "biological_type",
    "gender",
    "appearance",
    "relationship",
    "personality",
    "speaking_style",
    "voice",
    "user_call_name",
    "user_gender",
    "user_age_bucket",
    "user_hobbies",
    "user_freeform",
)
_ONBOARDING_MAX_LEN: int = 2000

# voice 之前的字段构成角色阶段，由唯一的顺序事实源派生。
_VOICE_FIELD_INDEX: int = ONBOARDING_FIELDS.index("voice")
_CHARACTER_ONBOARDING_FIELDS: tuple[str, ...] = ONBOARDING_FIELDS[:_VOICE_FIELD_INDEX]


class PersonaValidationError(ValueError):
    """人设校验失败；field 为出错字段名，结构性错误时为 None。"""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def load_persona_definition(persona: Persona | None) -> dict[str, str]:
    """从 Persona 实例读取 definition_json。"""
    if persona is None:
        return {}
    raw = getattr(persona, "definition_json", None) or "{}"
    draft = safe_json_loads(raw, default={})
    return draft if isinstance(draft, dict) else {}


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


async def get_or_create_persona(db: AsyncSession, user_id: int) -> Persona:
    """查询人设，不存在则暂存一条待插入行。刻意不 commit，以便调用方把 user_profile + persona 放在同一事务里写（ARCH §7.5）。"""
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    if persona is None:
        persona = Persona(user_id=user_id, definition_json="{}")
        db.add(persona)
        await db.flush()
    return persona


async def update_persona(db: AsyncSession, user_id: int, definition: dict[str, Any]) -> Persona:
    if not isinstance(definition, dict):
        raise PersonaValidationError("persona definition must be an object")
    user_profile = extract_user_profile(definition)
    persona_def = {k: v for k, v in definition.items() if not k.startswith("user_")}
    cleaned = _validate_definition(persona_def)

    async def _dual_write() -> Persona:
        await record_user_profile(db, MemoryScope(user_id, "companion"), user_profile)
        persona = await get_or_create_persona(db, user_id)
        current_draft = load_persona_definition(persona)
        if current_draft.get("voice"):
            cleaned["voice"] = current_draft["voice"]
        # DESIGN §5.4 形象锁定：形象确认后物种/性别/基础外貌不可再改——
        # 定稿后的保存把三字段静默替换回既有值（客户端表单本就不展示），
        # 且绝不重置 is_portrait_confirmed（§8 微调向导"不重置完成状态"）。
        # 草稿缺该键（历史上未填过）时同样丢弃新值：锁定字段的"既有值"不存在，不能凭 PUT 凭空立起来。
        if persona.is_portrait_confirmed:
            for locked in ("biological_type", "gender", "appearance"):
                if locked in current_draft:
                    cleaned[locked] = current_draft[locked]
                else:
                    cleaned.pop(locked, None)
        persona.definition_json = json.dumps(cleaned, ensure_ascii=False)
        # persona_extras 不缓存：build_system_prompt_extras 在运行期按 session language 从
        # definition_json 实时渲染，避免英语会话拿到 onboarding 时烤进去的中文头部。
        persona.is_complete = True
        return persona

    persona = await _dual_write()
    # 部分唯一索引冲突时重试：回滚会连带丢弃待提交的 persona 赋值，故整个双写重放（record_user_profile 幂等）
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        persona = await _dual_write()
        await db.commit()
    await db.refresh(persona)
    # onboarding 首次完成时一次性建出 5 套系统预设对话（companion/developer/pm/copywriter/language_teacher）；幂等。
    await ensure_system_conversations_for_user(db, persona.user_id)
    return persona


async def confirm_portrait(db: AsyncSession, user_id: int) -> Persona:
    persona = await get_or_create_persona(db, user_id)
    persona.is_portrait_confirmed = True
    persona.portrait_confirmed_at = func.now()
    await db.commit()
    await db.refresh(persona)
    return persona


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
    for key in _REQUIRED_FIELDS + _OPTIONAL_FIELDS:
        if key in definition:
            label = key.replace("_", " ").capitalize()
            lines.append(f"- **{label}**: {definition[key]}")
    return "\n".join(lines)


def _state(answers: dict[str, str], next_field: str | None, complete: bool) -> dict[str, Any]:
    return {"answers": answers, "next_field": next_field, "complete": complete}


async def get_onboarding_state(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """从数据库恢复引导进度；complete 以角色、头像、全身参考、外观参考立绘与音色为门槛。"""
    persona = await get_or_create_persona(db, user_id)
    draft = load_persona_definition(persona)
    if persona.is_complete:
        user_profile = await read_user_profile(db, MemoryScope(user_id, "companion"))
        merged = {**draft, **user_profile}
        if not persona.is_portrait_confirmed:
            return _state(merged, "portrait", False)
        avatar = (
            await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
        ).scalar_one_or_none()
        if avatar is None:
            return _state(merged, "portrait", False)
        if not avatar.seed_fullbody_url or not avatar.reference_image_url:
            return _state(merged, "fullbody-reference", False)
        if avatar.reference_image_url.startswith("temp-media/"):
            return _state(merged, "fullbody", False)
        if not draft.get("voice"):
            # 合并草稿与 Memory，让桌面端在音色阶段仍能预填已答资料。
            return _state(merged, "voice", False)
        # 用户资料均可跳过，且完成后可单独遗忘；缺失资料不能重启 onboarding。
        return _state({}, None, True)
    answers = draft
    missing_character = next((f for f in _CHARACTER_ONBOARDING_FIELDS if not answers.get(f)), None)
    if missing_character is not None:
        return _state(answers, missing_character, False)
    return _state(answers, "portrait", False)


async def submit_onboarding_field(db: AsyncSession, user_id: int, field: str, value: str | None) -> dict[str, Any]:
    """写入一条引导回答；is_complete 之后仅 user_*/voice 可改，角色字段须走 PUT /persona。"""
    if field not in ONBOARDING_FIELDS:
        raise PersonaValidationError(f"unknown onboarding field: {field!r}", field)
    persona = await get_or_create_persona(db, user_id)
    if persona.is_complete:
        # 后置阶段字段仍允许在此提交，详见单 PUT 双写契约
        if field.startswith("user_"):
            if value and value.strip():
                await record_user_profile(
                    db,
                    MemoryScope(user_id, "companion"),
                    {field: value.strip()[:_ONBOARDING_MAX_LEN]},
                )
                await db.commit()
            # 传空值不动 Memory 行：清除 user_* 条目通过记忆管理删除
            return _state(load_persona_definition(persona), None, True)
        # voice 不是人设字段，故此处只动草稿
        if field == "voice":
            draft = load_persona_definition(persona)
            if value and value.strip():
                draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
            else:
                draft.pop(field, None)
            persona.definition_json = json.dumps(draft, ensure_ascii=False)
            await db.commit()
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


async def set_render_mode(db: AsyncSession, *, user_id: int, render_mode: str) -> Persona:
    """写入渲染方式偏好；变化时广播 companion.render_mode.changed，客户端据此收敛各窗口显示。
    偏好只决定界面分区与目标形象的生成意图，不代表任何形象资产已经就绪。"""
    persona = await get_or_create_persona(db, user_id)
    previous = persona.render_mode
    persona.render_mode = render_mode
    if previous != render_mode:
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.render_mode.changed",
            payload={"new_mode": render_mode},
        )
    await db.commit()
    return persona
