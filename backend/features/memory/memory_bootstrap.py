from infrastructure.constants import DEFAULT_LANGUAGE
from infrastructure.text import resolve_prompt_text
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import MemoryScope, MemorySource
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.memory_store import active_memory_filter, scope_filter, upsert_slotted_memory
from features.memory.models import Memory
from features.memory.prompts import CONTEXT_LABELS, USER_PROFILE_LABELS_TEXTS

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


async def record_user_profile(
    db: AsyncSession,
    scope: MemoryScope,
    profile: dict[str, str],
) -> None:
    """保存引导回答；空值跳过，明确遗忘由记忆维护入口处理。"""
    changed = False
    for user_key, val in profile.items():
        if not val:
            continue
        ctx = CONTEXT_LABELS[user_key]
        await upsert_slotted_memory(db, scope, ctx, val, _USER_PROFILE_TAGS_JSON, source=MemorySource("onboarding"))
        changed = True
    if changed:
        emit_ws_event(
            db,
            user_id=scope.user_id,
            event_type="memory.changed",
            payload={"system_preset_id": scope.system_preset_id},
        )
