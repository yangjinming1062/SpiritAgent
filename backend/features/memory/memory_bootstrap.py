from infrastructure.constants import DEFAULT_LANGUAGE
from infrastructure.text import resolve_prompt_text
from runtime.memory_scope import MemoryScope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.memory_store import active_memory_filter, scope_filter
from features.memory.models import Memory
from features.memory.prompts import CONTEXT_LABELS, USER_PROFILE_LABELS_TEXTS


async def _active_profile_rows(db: AsyncSession, scope: MemoryScope) -> list[Memory]:
    rows = await db.scalars(
        select(Memory).where(scope_filter(scope), active_memory_filter(), Memory.kind == "user_profile")
    )
    return list(rows.all())


async def read_user_profile(db: AsyncSession, scope: MemoryScope) -> dict[str, str]:
    return {row.slot_key: row.content or "" for row in await _active_profile_rows(db, scope) if row.slot_key}


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
        display = row.context.replace("_", " ").capitalize()
        lines.append(f"- **{display}**: {row.content}")
    return "\n".join(lines)
