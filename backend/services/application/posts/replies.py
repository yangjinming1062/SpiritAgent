"""每条动态内串行、可恢复的评论回复。"""

import asyncio

from components import LLM_MAX_OUTPUT_TOKENS, SESSION_LOCAL, TaskBag, get_logger, resolve_prompt_text, track_user_task
from modules.companion import CompanionPostComment, PostContext
from prompts.posts import POST_REPLY_INSTRUCTIONS
from sqlalchemy import select

from services.domains.companion import load_companion_prompt_context
from services.domains.posts import emit_comment, get_post
from services.infrastructure.llm import call_llm_once, resolve_user_llm_config

logger = get_logger(__name__)
_BG = TaskBag("posts.replies")
_THREADS: dict[tuple[int, str], asyncio.Task[None]] = {}


def schedule_companion_reply(user_id: int, post_id: str) -> None:
    key = (user_id, post_id)
    if key in _THREADS and not _THREADS[key].done():
        return
    task = asyncio.create_task(_run_thread(user_id, post_id), name=f"post.reply.{post_id}")
    _THREADS[key] = task
    task.add_done_callback(lambda done: _THREADS.pop(key, None) if _THREADS.get(key) is done else None)
    _BG.add(task)
    track_user_task(user_id, task)


async def _run_thread(user_id: int, post_id: str) -> None:
    while True:
        async with SESSION_LOCAL() as db:
            target = await db.scalar(
                select(CompanionPostComment)
                .where(
                    CompanionPostComment.user_id == user_id,
                    CompanionPostComment.post_id == post_id,
                    CompanionPostComment.role == "user",
                    CompanionPostComment.reply_status.in_(("pending", "running")),
                )
                .order_by(CompanionPostComment.created_at, CompanionPostComment.id)
                .limit(1),
            )
        if target is None:
            return
        try:
            await _generate_reply(user_id, post_id, target.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Post reply failed", extra={"post_id": post_id, "comment_id": target.id})
            async with SESSION_LOCAL() as db:
                row = await db.scalar(
                    select(CompanionPostComment).where(CompanionPostComment.id == target.id).with_for_update(),
                )
                if row is not None and row.reply_status in ("pending", "running"):
                    row.reply_status, row.reply_error = "failed", "回复暂未完成，可以重试"
                    await emit_comment(db, row)
                    await db.commit()


async def _generate_reply(user_id: int, post_id: str, comment_id: str) -> None:
    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        raise RuntimeError("Companion is not ready")
    async with SESSION_LOCAL() as db:
        target = await db.scalar(
            select(CompanionPostComment)
            .where(
                CompanionPostComment.id == comment_id,
                CompanionPostComment.user_id == user_id,
            )
            .with_for_update(),
        )
        if target is None:
            return
        post = await get_post(db, user_id, post_id)
        if post is None:
            return
        target.reply_status, target.reply_error = "running", None
        target.reply_attempts += 1
        attempt = target.reply_attempts
        await emit_comment(db, target)
        allowed_users = {
            c.id for c in post.comments if c.role == "user" and (c.created_at, c.id) <= (target.created_at, target.id)
        }
        payload = {
            "output_language": ctx.language,
            "current_time": ctx.current_time,
            "persona": ctx.persona_extras,
            "long_term_memories": ctx.memories_block,
            "current_mood": ctx.current_mood,
            "post": {
                "title": post.title,
                "body": post.body,
                "content_type": post.content_type,
                "published_at": post.published_at.isoformat(),
            },
            "media_context": PostContext.model_validate(post.context_json).model_dump(exclude={"voice_id"}),
            "target_comment": {"id": target.id, "role": "user", "content": target.content},
            "comments": [
                {"role": c.role, "content": c.content}
                for c in post.comments
                if c.id in allowed_users or (c.role == "companion" and c.reply_to_comment_id in allowed_users)
            ],
        }
        config = await resolve_user_llm_config(db, user_id)
        await db.commit()
    if not config.is_configured:
        raise RuntimeError("LLM is not configured")
    reply = (
        await call_llm_once(
            config,
            resolve_prompt_text(POST_REPLY_INSTRUCTIONS, ctx.language),
            payload,
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        )
    ).strip()
    if not reply or len(reply) > 500:
        raise ValueError("Empty or oversized comment reply")
    async with SESSION_LOCAL() as db:
        target = await db.scalar(
            select(CompanionPostComment)
            .where(
                CompanionPostComment.id == comment_id,
                CompanionPostComment.user_id == user_id,
            )
            .with_for_update(),
        )
        if target is None or target.reply_attempts != attempt or target.reply_status != "running":
            return
        existing = await db.scalar(
            select(CompanionPostComment).where(CompanionPostComment.reply_to_comment_id == comment_id),
        )
        if existing is None:
            row = CompanionPostComment(
                user_id=user_id,
                post_id=post_id,
                role="companion",
                content=reply,
                reply_to_comment_id=comment_id,
                reply_status="none",
            )
            db.add(row)
            await emit_comment(db, row)
        target.reply_status, target.reply_error = "completed", None
        await emit_comment(db, target)
        await db.commit()


async def resume_replies() -> None:
    async with SESSION_LOCAL() as db:
        rows = (
            await db.execute(
                select(CompanionPostComment.user_id, CompanionPostComment.post_id)
                .where(
                    CompanionPostComment.role == "user",
                    CompanionPostComment.reply_status.in_(("pending", "running")),
                )
                .distinct(),
            )
        ).all()
    for user_id, post_id in rows:
        schedule_companion_reply(user_id, post_id)


async def drain_replies() -> None:
    await _BG.drain()
