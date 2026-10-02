from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import DEFAULT_LANGUAGE, resolve_prompt_text
from modules.memory import Memory
from modules.settings import get_user_setting, put_user_settings
from prompts.memory import CONTEXT_LABELS, USER_PROFILE_LABELS_TEXTS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope, MemorySource

from .memory_store import active_memory_filter, scope_filter, upsert_slotted_memory

_USER_PROFILE_TAGS_JSON = '["onboarding", "user_profile"]'

# CONTEXT_LABELS 的值→键反查表：read_user_profile 用它把 context 槽位还原为 user_* 原始键。
_REVERSE_CONTEXT_LABELS: dict[str, str] = {v: k for k, v in CONTEXT_LABELS.items()}


async def _active_profile_rows(db: AsyncSession, scope: MemoryScope) -> list[Memory]:
    rows = await db.scalars(
        select(Memory).where(
            scope_filter(scope),
            active_memory_filter(),
            Memory.context.like("user_profile:%"),
        ),
    )
    return list(rows.all())


async def read_user_profile(db: AsyncSession, scope: MemoryScope) -> dict[str, str]:
    """record_user_profile 的逆操作：以 {raw_key: content} 返回用户当前的 user_* 回答。"""
    out: dict[str, str] = {}
    for row in await _active_profile_rows(db, scope):
        suffix = row.context.split(":", 1)[1]
        raw_key = _REVERSE_CONTEXT_LABELS.get(row.context, f"user_{suffix}")
        out[raw_key] = row.content or ""
    return out


async def build_user_profile_extras(db: AsyncSession, scope: MemoryScope, *, language: str = DEFAULT_LANGUAGE) -> str:
    rows = await _active_profile_rows(db, scope)
    if not rows:
        return ""
    # 已知字段按声明顺序、其余按字典序渲染，保证给 LLM 的形状稳定
    by_ctx = {row.context: row for row in rows}
    known_ctxs = list(CONTEXT_LABELS.values())
    ordered = [by_ctx[c] for c in known_ctxs if c in by_ctx] + [
        by_ctx[c] for c in sorted(by_ctx) if c not in known_ctxs
    ]
    lines = [resolve_prompt_text(USER_PROFILE_LABELS_TEXTS, language)]
    for row in ordered:
        display = row.context.split(":", 1)[1].replace("_", " ").capitalize()
        lines.append(f"- **{display}**: {row.content}")
    return "\n".join(lines)


async def record_user_profile(db: AsyncSession, scope: MemoryScope, profile: dict[str, str]) -> None:
    """把 user_* 字段写入 Memory；空值跳过（不插不删），使人设编辑器可重复保存而不清空既有行。"""
    for user_key, val in profile.items():
        if not val:
            continue
        ctx = CONTEXT_LABELS.get(user_key, f"user_profile:{user_key.removeprefix('user_')}")
        await upsert_slotted_memory(db, scope, ctx, val, _USER_PROFILE_TAGS_JSON, source=MemorySource("onboarding"))


async def resolve_user_timezone(db: AsyncSession, user_id: int) -> str | None:
    """读用户 IANA 时区；夜间批处理与互动统计按它做本地日聚合。"""
    tz = await get_user_setting(db, user_id, "timezone")
    return tz if isinstance(tz, str) and tz else None


async def record_user_timezone(db: AsyncSession, user_id: int, tz: str) -> bool:
    """校验并落盘时区；非法 IANA 名拒绝（返回 False），避免毒值让夜间窗口永远跳过。"""
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
    await put_user_settings(db, user_id, {"timezone": tz})
    return True
