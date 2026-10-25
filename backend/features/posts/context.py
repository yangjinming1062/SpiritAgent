"""动态创作与评论回复共用的陪伴资料及事件时间。"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, format_local_iso
from runtime.memory_scope import MemoryScope

from features.companion.prompt_runtime import load_companion_prompt_context
from features.conversation.context_window import load_companion_dialogue
from features.entries.contracts import EntryView
from features.memory.memory_bootstrap import build_user_profile_extras
from features.memory.memory_format import format_companion_reflection_block
from features.posts.schemas import PostContext
from features.posts.store import PostError


@dataclass(frozen=True)
class PostPromptContext:
    now: datetime
    user_timezone: str
    language: str
    payload: dict[str, Any]


async def load_post_prompt_context(user_id: int) -> PostPromptContext:
    companion = await load_companion_prompt_context(user_id)
    if companion is None:
        raise PostError("伙伴资料尚未就绪")
    now = datetime.fromisoformat(companion.current_time)
    scope = MemoryScope(user_id, "companion")
    async with SESSION_LOCAL() as db:
        dialogue = await load_companion_dialogue(db, user_id, now=now)
        profile = await build_user_profile_extras(db, scope, language=companion.language)
        reflection = await format_companion_reflection_block(db, scope, language=companion.language)
    return PostPromptContext(
        now=ensure_utc(now),
        user_timezone=dialogue.user_timezone,
        language=companion.language,
        payload={
            "output_language": companion.language,
            "current_time": dialogue.window_end.isoformat(),
            "user_timezone": dialogue.user_timezone,
            "timezone_configured": dialogue.timezone_configured,
            "persona": companion.persona_extras,
            "user_profile": profile,
            "long_term_memories": companion.memories_block,
            "companion_reflection": reflection,
            "life_context": companion.life_context.prompt_payload(),
            "recent_dialogue": {
                "window_start": dialogue.window_start.isoformat(),
                "window_end": dialogue.window_end.isoformat(),
                "messages": json.loads(dialogue.content or "[]"),
            },
        },
    )


def elapsed_seconds(later: datetime, earlier: datetime) -> int:
    return int((ensure_utc(later) - ensure_utc(earlier)).total_seconds())


def post_prompt_entry(post: EntryView, context: PostPromptContext) -> dict[str, Any]:
    return {
        "id": post.id,
        "title": post.title,
        "body": post.body,
        "content_type": post.content_type,
        "published_at": format_local_iso(post.published_at, context.user_timezone),
        "seconds_since_publication": elapsed_seconds(context.now, post.published_at),
        "media_context": PostContext.model_validate(post.context_json).model_dump(exclude={"voice_id"}),
    }
