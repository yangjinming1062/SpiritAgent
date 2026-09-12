from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import DEFAULT_LANGUAGE, resolve_prompt_text
from modules.memory import Memory
from modules.settings import UserSetting
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts.memory import MemoryScope, MemorySource

from .memory_store import active_memory_filter, scope_filter, upsert_slotted_memory

_USER_PROFILE_TAGS_JSON = '["onboarding", "user_profile"]'

# 已知 user_* 键的友好上下文标签；未知键回落为 user_profile:<raw_key>
_CONTEXT_LABELS: dict[str, str] = {
    "user_call_name": "user_profile:preferred_name",
    "user_gender": "user_profile:gender",
    "user_age_bucket": "user_profile:age_bucket",
    "user_hobbies": "user_profile:hobbies",
    "user_freeform": "user_profile:freeform",
}

# 双语用户资料块标题；display 字段（context.split(":",1)[1].replace("_"," ").capitalize()）属协议级展示，保持英文不译。
_USER_PROFILE_LABELS_TEXTS: dict[str, str] = {
    "zh": "# 用户资料",
    "en": "# User profile",
}

_REVERSE_CONTEXT_LABELS: dict[str, str] = {v: k for k, v in _CONTEXT_LABELS.items()}


def extract_user_profile(payload: dict[str, Any]) -> dict[str, str]:
    return {k: (payload.get(k) or "").strip() for k in payload if k.startswith("user_")}


async def read_user_profile(db: AsyncSession, scope: MemoryScope) -> dict[str, str]:
    """record_user_profile 的逆操作：以 {raw_key: content} 返回用户当前的 user_* 回答。"""
    rows = (
        (
            await db.execute(
                select(Memory).where(
                    scope_filter(scope),
                    active_memory_filter(),
                    Memory.context.like("user_profile:%"),
                ),
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, str] = {}
    for row in rows:
        suffix = row.context.split(":", 1)[1]
        raw_key = _REVERSE_CONTEXT_LABELS.get(row.context, f"user_{suffix}")
        out[raw_key] = row.content or ""
    return out


async def build_user_profile_extras(db: AsyncSession, scope: MemoryScope, *, language: str = DEFAULT_LANGUAGE) -> str:
    rows = (
        (
            await db.execute(
                select(Memory).where(
                    scope_filter(scope),
                    active_memory_filter(),
                    Memory.context.like("user_profile:%"),
                ),
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return ""
    # 已知字段按声明顺序、其余按字典序渲染，保证给 LLM 的形状稳定
    by_ctx = {row.context: row for row in rows}
    known_ctxs = list(_CONTEXT_LABELS.values())
    ordered = [by_ctx[c] for c in known_ctxs if c in by_ctx] + [
        by_ctx[c] for c in sorted(by_ctx) if c not in known_ctxs
    ]
    lines = [resolve_prompt_text(_USER_PROFILE_LABELS_TEXTS, language)]
    for row in ordered:
        display = row.context.split(":", 1)[1].replace("_", " ").capitalize()
        lines.append(f"- **{display}**: {row.content}")
    return "\n".join(lines)


async def record_user_profile(db: AsyncSession, scope: MemoryScope, profile: dict[str, str]) -> None:
    """把 user_* 字段写入 Memory；空值跳过（不插不删），使人设编辑器可重复保存而不清空既有行。"""
    for user_key, val in profile.items():
        if not val:
            continue
        ctx = _CONTEXT_LABELS.get(user_key, f"user_profile:{user_key.removeprefix('user_')}")
        await upsert_slotted_memory(db, scope, ctx, val, _USER_PROFILE_TAGS_JSON, source=MemorySource("onboarding"))


async def resolve_user_timezone(db: AsyncSession, user_id: int) -> str | None:
    """读用户 IANA 时区；夜间批处理与互动统计按它做本地日聚合。"""
    val = (
        await db.execute(
            select(UserSetting.setting_value).where(
                UserSetting.user_id == user_id,
                UserSetting.setting_key == "timezone",
            ),
        )
    ).scalar()
    return (val or "").strip() or None


async def record_user_timezone(db: AsyncSession, user_id: int, tz: str) -> bool:
    """校验并落盘时区；非法 IANA 名拒绝（返回 False），避免毒值让夜间窗口永远跳过。"""
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
    await db.execute(
        insert(UserSetting)
        .values(user_id=user_id, setting_key="timezone", setting_value=tz)
        .on_conflict_do_update(
            index_elements=["user_id", "setting_key"],
            set_={"setting_value": tz, "updated_at": func.now()},
        ),
    )
    return True
