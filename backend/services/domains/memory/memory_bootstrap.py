from components import DEFAULT_LANGUAGE, resolve_prompt_text
from modules.memory import Memory
from modules.ws import emit_ws_event
from prompts.memory import CONTEXT_LABELS, USER_PROFILE_LABELS_TEXTS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope, MemorySource

from .memory_store import active_memory_filter, memory_write_lock, scope_filter, upsert_slotted_memory

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
    *,
    expected_versions: dict[str, int] | None = None,
) -> None:
    """把 user_* 字段写入 Memory；空值跳过（不插不删），使人设编辑器可重复保存而不清空既有行。"""
    changed = False
    for user_key, val in profile.items():
        if not val:
            continue
        ctx = CONTEXT_LABELS.get(user_key, f"user_profile:{user_key.removeprefix('user_')}")
        if expected_versions is not None and user_key in expected_versions:
            # 与编辑、遗忘共用事务写锁；0 表示开始编辑时没有有效槽位。
            await memory_write_lock(db, scope)
            current_version = await db.scalar(
                select(Memory.content_version).where(
                    scope_filter(scope),
                    active_memory_filter(),
                    Memory.context == ctx,
                ),
            )
            if (current_version or 0) != expected_versions[user_key]:
                raise ValueError("这条资料已在其他设备更新，请刷新后再保存。")
        await upsert_slotted_memory(db, scope, ctx, val, _USER_PROFILE_TAGS_JSON, source=MemorySource("onboarding"))
        changed = True
    if changed:
        emit_ws_event(
            db,
            user_id=scope.user_id,
            event_type="memory.changed",
            payload={"system_preset_id": scope.system_preset_id},
        )
