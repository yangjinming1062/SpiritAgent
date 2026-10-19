"""每条动态内串行、可恢复的评论回复。"""

import asyncio

from infrastructure.ai.error_classifier import ClassifiedError, FailoverReason, LLMRuntimeError
from infrastructure.ai.prompt_engineer import call_llm_once
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import resolve_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import format_local_iso
from runtime.background import TaskBag
from sqlalchemy import select

from features.posts.context import elapsed_seconds, load_post_prompt_context, post_prompt_entry
from features.posts.models import CompanionPostComment
from features.posts.prompt_contract import render_post_instructions
from features.posts.prompts import POST_REPLY_INSTRUCTIONS
from features.posts.schemas import POST_COMMENT_MAX_CHARS
from features.posts.store import emit_comment, get_post

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
    _BG.add(task, user_id=user_id)


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


def _validate_reply(text: str) -> None:
    reply = text.strip()
    if not reply or len(reply) > POST_COMMENT_MAX_CHARS:
        raise LLMRuntimeError(ClassifiedError(FailoverReason.format_error, None, "Empty or oversized comment reply"))


async def _generate_reply(user_id: int, post_id: str, comment_id: str) -> None:
    ctx = await load_post_prompt_context(user_id)
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
        thread = [
            c
            for c in post.comments
            if c.id in allowed_users or (c.role == "companion" and c.reply_to_comment_id in allowed_users)
        ]
        post_data = post_prompt_entry(post, ctx)
        payload = {
            **ctx.payload,
            "media_context": post_data.pop("media_context"),
            "post": post_data,
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
        config = await resolve_user_llm_config(db, user_id, AiTaskKey.posts)
        await db.commit()
    if not config.is_configured:
        raise RuntimeError("LLM is not configured")
    reply = (
        await call_llm_once(
            config,
            render_post_instructions(POST_REPLY_INSTRUCTIONS, ctx.language),
            payload,
            validate_output=_validate_reply,
        )
    ).strip()
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
