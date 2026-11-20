"""每条动态内串行、可恢复的评论回复。"""

import asyncio
from typing import Literal, Self

from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import resolve_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import ensure_utc, format_local_iso, utc_now
from pydantic import BaseModel, ConfigDict, Field, model_validator
from runtime.background import TaskScope

from features.companion.intent_contracts import DeferredReply
from features.entries.queries import comments_for_entries, pending_reply, pending_reply_threads
from features.entries.service import begin_reply, fail_reply, finish_reply
from features.posts.context import elapsed_seconds, load_post_prompt_context, post_prompt_entry
from features.posts.prompt_contract import render_post_instructions
from features.posts.prompts import POST_REPLY_INSTRUCTIONS
from features.posts.schemas import POST_COMMENT_MAX_CHARS
from features.posts.store import get_post

logger = get_logger(__name__)
_THREADS = TaskScope("posts.replies")


def schedule_companion_reply(user_id: int, post_id: int) -> None:
    _THREADS.spawn(
        (user_id, post_id), lambda: _run_thread(user_id, post_id), name=f"post.reply.{post_id}", user_id=user_id
    )


async def _run_thread(user_id: int, post_id: int) -> None:
    while True:
        async with SESSION_LOCAL() as db:
            target = await pending_reply(db, user_id, post_id)
        if target is None:
            return
        try:
            await _generate_reply(user_id, post_id, target.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Post reply failed", extra={"post_id": post_id, "comment_id": target.id})
            async with SESSION_LOCAL() as db:
                await fail_reply(db, user_id, target.id)
                await db.commit()


class CommentReplyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["reply", "quiet", "defer"]
    text: str | None = Field(default=None, min_length=1, max_length=POST_COMMENT_MAX_CHARS)
    followup: DeferredReply | None = None

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if (self.kind == "reply") != (self.text is not None) or (self.kind == "defer") != (self.followup is not None):
            raise ValueError("Reply requires text, defer requires followup, and quiet has neither")
        return self


async def _generate_reply(user_id: int, post_id: int, comment_id: int) -> None:
    async with SESSION_LOCAL() as db:
        target = await begin_reply(db, user_id, post_id, comment_id)
        if target is None:
            return
        post = await get_post(db, user_id, post_id)
        if post is None:
            return
        attempt = target.reply_attempts
        deadline = ensure_utc(target.reply_expires_at) if target.reply_expires_at is not None else None
        if deadline is not None and utc_now() >= deadline:
            await finish_reply(db, user_id, post_id, comment_id, attempt, None)
            await db.commit()
            return
        comments = (await comments_for_entries(db, user_id, [post_id])).get(post_id, [])
        allowed_users = {
            c.id
            for c in comments
            if c.role == "user"
            and (target.reply_followup is not None or (c.created_at, c.id) <= (target.created_at, target.id))
        }
        thread = [
            c
            for c in comments
            if c.id in allowed_users or (c.role == "companion" and c.reply_to_comment_id in allowed_users)
        ]
        config = await resolve_user_llm_config(db, user_id, AiTaskKey.posts)
        await db.commit()
    ctx = await load_post_prompt_context(user_id, memory_query=target.content + " " + post.title)
    post_data = post_prompt_entry(post, ctx)
    payload = {
        **ctx.payload,
        "media_context": post_data.pop("media_context"),
        "post": post_data,
        "previous_followup": target.reply_followup,
        "original_expiry": format_local_iso(deadline, ctx.user_timezone) if deadline is not None else None,
        "target_comment": {
            "id": target.id,
            "role": "user",
            "content": target.content,
            "created_at": format_local_iso(target.created_at, ctx.user_timezone),
            "seconds_after_publication": elapsed_seconds(target.created_at, post.published_at),
            "seconds_until_reply": elapsed_seconds(ctx.now, target.created_at),
        },
        "comments": [
            {
                "id": c.id,
                "reply_to_comment_id": c.reply_to_comment_id,
                "role": c.role,
                "content": c.content,
                "created_at": format_local_iso(c.created_at, ctx.user_timezone),
                "seconds_after_previous_comment": elapsed_seconds(c.created_at, thread[i - 1].created_at)
                if i
                else None,
            }
            for i, c in enumerate(thread)
        ],
    }
    if not config.is_configured:
        raise RuntimeError("LLM is not configured")

    def parse_reply(value: object) -> CommentReplyDecision:
        result = CommentReplyDecision.model_validate(value)
        if result.followup and deadline is not None and result.followup.bounds(ctx.now)[0] >= deadline:
            raise ValueError("Followup exceeds the remaining time")
        return result

    reply = await call_llm_json(
        config,
        render_post_instructions(POST_REPLY_INSTRUCTIONS, ctx.language),
        payload,
        schema=CommentReplyDecision.model_json_schema(),
        schema_name="companion_comment_reply",
        parse_output=parse_reply,
        repair_prompt="Preserve the original kind and content or followup. "
        "For defer, preserve both offsets relative to the decision and keep the wake time before original_expiry.",
        preserve_choices=("kind",),
    )
    async with SESSION_LOCAL() as db:
        if reply.followup is not None:
            due, expiry = reply.followup.bounds(utc_now())
            await finish_reply(
                db,
                user_id,
                post_id,
                comment_id,
                attempt,
                None,
                deferred_until=due,
                expires_at=expiry,
                purpose=reply.followup.purpose,
            )
        else:
            await finish_reply(db, user_id, post_id, comment_id, attempt, reply.text)
        await db.commit()


async def resume_replies() -> None:
    async with SESSION_LOCAL() as db:
        rows = await pending_reply_threads(db)
    for user_id, post_id in rows:
        schedule_companion_reply(user_id, post_id)


async def drain_replies() -> None:
    await _THREADS.drain()
