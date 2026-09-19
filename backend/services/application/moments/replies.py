"""片刻评论的精灵回复：用户评论一条片刻后，后台生成一句第一人称回应并写回评论区。

回复属用户发起交互的响应，不受打扰档位拦截；它不进主对话、不发 companion.message，
只写 role=companion 评论行并经 ``companion.moment.comment`` 事件刷新信息流。
"""

import asyncio

from components import SESSION_LOCAL, get_logger, resolve_prompt_text, track_user_task
from modules.companion import MomentCommentRole
from prompts.nightly import MOMENT_REPLY_INSTRUCTIONS

from services.domains.companion import load_companion_prompt_context
from services.domains.journal import create_moment_comment, get_moment
from services.infrastructure.llm import call_llm_once, resolve_user_llm_config

logger = get_logger(__name__)

_MAX_REPLY_TOKENS = 300


def schedule_companion_reply(user_id: int, moment_id: str) -> None:
    """在请求返回后异步生成精灵回复；失败只记日志，用户可再次评论。"""
    task = asyncio.create_task(_generate_reply(user_id, moment_id))
    track_user_task(user_id, task)


async def _generate_reply(user_id: int, moment_id: str) -> None:
    try:
        await _generate_reply_inner(user_id, moment_id)
    except Exception:
        logger.warning(
            "moment reply generation failed",
            extra={"user_id": user_id, "moment_id": moment_id},
            exc_info=True,
        )


async def _generate_reply_inner(user_id: int, moment_id: str) -> None:
    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return
    async with SESSION_LOCAL() as db:
        moment = await get_moment(db, user_id, moment_id)
        if moment is None:
            return
        payload = {
            "output_language": ctx.language,
            "persona": ctx.persona_extras,
            "moment": {
                "title": moment.title,
                "body": moment.body,
                "kind": moment.kind,
                **({"emotion": moment.emotion} if moment.emotion else {}),
            },
            "comments": [{"role": c.role, "content": c.content} for c in (moment.comments or [])],
        }
        llm_cfg = await resolve_user_llm_config(db, user_id)
    if not llm_cfg.model_name:
        return
    if ctx.memories_block:
        payload["long_term_memories"] = ctx.memories_block
    if ctx.current_mood:
        payload["current_mood"] = ctx.current_mood

    reply = (
        await call_llm_once(
            llm_cfg,
            resolve_prompt_text(MOMENT_REPLY_INSTRUCTIONS, ctx.language),
            payload,
            max_output_tokens=_MAX_REPLY_TOKENS,
        )
        or ""
    ).strip()
    if not reply:
        logger.info("moment reply: empty response", extra={"user_id": user_id})
        return

    async with SESSION_LOCAL() as db:
        row = await create_moment_comment(
            db,
            user_id,
            moment_id,
            content=reply,
            role=MomentCommentRole.COMPANION.value,
        )
    logger.info("moment reply: written", extra={"user_id": user_id, "comment_id": row.id})
