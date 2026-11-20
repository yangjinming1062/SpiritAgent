"""动态发布事务；内容、评论和附件通过 entries 领域操作。"""

import asyncio
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from infrastructure.ai.token_budget import json_tokens
from infrastructure.assets.asset_store import signed_companion_asset_url
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.text import ensure_utc, format_local_iso, local_date, utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from features.accounts.locking import lock_account
from features.accounts.queries import user_is_active
from features.appearance.identity import visual_identity_is_current
from features.configuration.settings.timezone import resolve_user_timezone
from features.configuration.settings.values import get_user_setting_bool
from features.entries import queries as entries_queries
from features.entries import service as entries_service
from features.entries.contracts import CommentView, EntryError, EntryNotFoundError, EntryView, PostContentType
from features.jobs.service import bind_job_request, requested_job_id, requested_job_ids
from features.life.context import previous_post_excerpt_pages
from features.media.continuations import finish_media_operation
from features.media.identity_contracts import VisualIdentitySnapshot
from features.media.review_contracts import MediaSelection, VisualReview
from features.media.review_evidence import review_media_intact
from features.posts.ports import notify_post_published
from features.posts.publication_models import PostPublication
from features.posts.schemas import PostCommentResponse, PostContext, PostPublicationResult, PostResponse


class PostError(RuntimeError):
    pass


class PostNotFoundError(PostError):
    pass


class PostBlockedError(PostError):
    """当前发布政策或交付条件阻止了本次发布。"""


@dataclass(frozen=True)
class PostInteractions:
    threads: list[dict]
    posted_ids: list[int]


def response_for_comment(row: CommentView) -> PostCommentResponse:
    return PostCommentResponse.model_validate(row, from_attributes=True)


def publication_status(row: PostPublication) -> str:
    return row.outcome or ("result_unknown" if row.state == "unknown" else row.state)


def response_for_publication(row: PostPublication) -> PostPublicationResult:
    return PostPublicationResult(
        publication_id=row.id, status=publication_status(row), post_id=row.post_id, error=row.error
    )


def response_for_post(row: EntryView) -> PostResponse:
    return PostResponse.model_validate(row, from_attributes=True).model_copy(
        update={
            "media_url": signed_companion_asset_url(row.media_url) if row.media_url else None,
            "audio_url": signed_companion_asset_url(row.audio_url) if row.audio_url else None,
        }
    )


async def get_post(db: AsyncSession, user_id: int, post_id: int) -> EntryView | None:
    return await entries_queries.get_entry(db, user_id, post_id, kind="post")


async def has_unread_posts(db: AsyncSession, user_id: int) -> bool:
    return await entries_queries.has_unread_entries(db, user_id, "post")


async def mark_posts_read(db: AsyncSession, user_id: int, post_ids: Sequence[int]) -> bool:
    unread = await entries_service.mark_entries_read(db, user_id, "post", post_ids)
    await db.commit()
    return unread


async def list_posts(
    db: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int = 20
) -> tuple[list[EntryView], str | None, list[int]]:
    try:
        return await entries_queries.list_posts(db, user_id, cursor=cursor, limit=limit)
    except EntryError as exc:
        raise PostError(str(exc)) from exc


async def posts_policy_ok(db: AsyncSession, user_id: int) -> bool:
    return await user_is_active(db, user_id) and await get_user_setting_bool(
        db, user_id, "companion.posts_enabled", default=True
    )


def fit_post_choice(item: dict[str, Any], room: int) -> tuple[dict[str, Any] | None, int]:
    """完整正文放不下时仍尽量交代选择和发表事实。"""
    cost = json_tokens(item) + 2
    if cost <= room:
        return item, cost
    compact = {k: v for k, v in item.items() if k not in {"published_post", "narrative"}}
    cost = json_tokens(compact) + 2
    return (compact, cost) if cost <= room else (None, 0)


async def post_choice_history(
    db: AsyncSession,
    user_id: int,
    source_ids: Collection[int],
    *,
    token_budget: int,
    exclude: Collection[int] = (),
) -> tuple[list[dict[str, Any]], bool]:
    """从最近的选择开始分页，达到模型输入余量即停；同一成稿的正文只取一次。"""
    history: list[dict[str, Any]] = []
    posts: dict[int, tuple[set[int] | None, dict[str, Any]]] = {}
    included_posts: set[str] = set()
    excerpted = False
    async for excerpts in previous_post_excerpt_pages(db, user_id, source_ids, exclude=exclude):
        requests = {f"post-excerpt:{item['id']}": item for item in excerpts}
        job_ids = await requested_job_ids(db, user_id, requests.keys())
        jobs = {
            row.id: row
            for row in await db.scalars(
                select(PostPublication)
                .options(load_only(PostPublication.state, PostPublication.outcome, PostPublication.post_id))
                .where(PostPublication.user_id == user_id, PostPublication.id.in_(job_ids.values()))
            )
        }
        missing = {job.post_id for job in jobs.values() if job.post_id is not None} - posts.keys()
        if missing:
            entries = await entries_queries.selected_posts(db, user_id, list(missing))
            for post in entries.values():
                context = PostContext.model_validate(post.context_json)
                used = set(context.used_excerpt_ids) if context.used_excerpt_ids is not None else None
                posts[post.id] = (
                    used,
                    {
                        "id": str(post.id),
                        "published_at": post.published_at.isoformat(),
                        "title": post.title,
                        "body": post.body,
                        "transcript": context.transcript,
                        "narration": context.narration,
                    },
                )
        for request, excerpt in requests.items():
            job = jobs.get(job_ids[request]) if request in job_ids else None
            item: dict[str, Any] = {**excerpt, "planning_status": publication_status(job) if job else "not_requested"}
            if job and job.post_id in posts:
                used, content = posts[job.post_id]
                item["post_id"] = str(job.post_id)
                item["used_in_post"] = int(excerpt["id"]) in used if used is not None else None
                if item["post_id"] not in included_posts:
                    item["published_post"] = content
            kept, cost = fit_post_choice(item, token_budget)
            if kept is None:
                return history, True
            if "published_post" in kept:
                included_posts.add(kept["post_id"])
            excerpted |= kept is not item
            history.append(kept)
            token_budget -= cost
    return history, excerpted


async def next_planning_publication(
    db: AsyncSession, user_id: int, *, collecting_only: bool = False
) -> PostPublication | None:
    states = ("queued",) if collecting_only else ("queued", "running")
    return await db.scalar(
        select(PostPublication)
        .where(
            PostPublication.user_id == user_id, PostPublication.stage == "planning", PostPublication.state.in_(states)
        )
        .order_by(PostPublication.id)
        .limit(1)
    )


async def prepare_publication_in_session(
    db: AsyncSession, user_id: int, *, excerpt_id: int | None = None
) -> PostPublicationResult | None:
    """摘选与接受它的任务在调用者事务内一起提交；运行中的规划已冻结输入。"""
    await lock_account(db, user_id)
    if not await posts_policy_ok(db, user_id):
        return None
    request_id = f"post-excerpt:{excerpt_id}" if excerpt_id is not None else None
    if request_id and (previous := await requested_job_id(db, user_id, request_id)) is not None:
        row = await db.get(PostPublication, previous)
        if row is None:
            raise PostError("摘选关联的动态任务不存在")
        return response_for_publication(row)
    row = await next_planning_publication(db, user_id, collecting_only=excerpt_id is not None)
    if row is None:
        timezone = await resolve_user_timezone(db, user_id)
        row = PostPublication(
            user_id=user_id,
            activity_date=local_date(utc_now(), timezone),
            request_json={},
            checkpoint={},
            state="queued",
            stage="planning",
        )
        db.add(row)
        await db.flush()
    if request_id and excerpt_id is not None:
        row.checkpoint = {**row.checkpoint, "excerpt_ids": [*row.checkpoint.get("excerpt_ids", []), excerpt_id]}
        await bind_job_request(db, user_id, row.id, request_id, str(excerpt_id))
    return response_for_publication(row)


async def commit_publication(
    db: AsyncSession,
    task_id: int,
    user_id: int,
    *,
    title: str,
    body: str,
    content_type: PostContentType,
    media_url: str | None,
    audio_url: str | None,
    context: PostContext,
    partial: bool,
) -> EntryView:
    await lock_account(db, user_id)
    task = await db.scalar(
        select(PostPublication)
        .where(PostPublication.id == task_id, PostPublication.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise PostNotFoundError("发布任务已不存在")
    if task.post_id is not None:
        row = await get_post(db, task.user_id, task.post_id)
        if row is None:
            raise PostError("发布记录缺少动态")
        await notify_post_published(db, row)
        await db.commit()
        return row
    if task.state in {"failed", "cancelled"} or not await posts_policy_ok(db, task.user_id):
        raise PostBlockedError("发布条件已变化")
    progress = task.checkpoint or {}
    depicts_self = bool((task.plan_json or {}).get("depicts_self"))
    identity = progress.get("identity")
    if depicts_self and content_type in {PostContentType.IMAGE, PostContentType.VIDEO} and not identity:
        raise PostBlockedError("缺少本次出镜身份")
    if identity and not await visual_identity_is_current(
        db, task.user_id, VisualIdentitySnapshot.model_validate(identity)
    ):
        raise PostBlockedError("发布时角色身份已变化")
    if content_type != PostContentType.TEXT and not media_url:
        raise PostError("主媒体尚未完成，不能发布动态")
    if content_type == PostContentType.TEXT and (media_url or audio_url):
        raise PostError("文字动态不附带媒体")
    if audio_url and content_type not in (PostContentType.IMAGE, PostContentType.VIDEO):
        raise PostError("只有图片或视频动态可以添加旁白")
    for path in (media_url, audio_url):
        if path is not None and (not (parsed := parse_companion_asset_path(path)) or parsed[0] != task.user_id):
            raise PostError("动态只能引用本账户的正式媒体资产")
    if content_type in {PostContentType.IMAGE, PostContentType.VIDEO}:
        required = ("identity", "goal", "quality") if depicts_self else ("goal", "quality")
        if content_type == PostContentType.VIDEO:
            required = (*required, "opening")
        if (
            progress.get("media_url") != media_url
            or not progress.get("media_review")
            or not progress.get("media_selection")
        ):
            raise PostError("原制作任务缺少该成品的交付依据")
        selection = MediaSelection.model_validate(progress["media_selection"])
        media_review = VisualReview.model_validate(progress["media_review"])
        if not selection.permits(media_review, required) or not await asyncio.to_thread(
            review_media_intact, media_review.evidence, media_url
        ):
            raise PostError("媒体检查尚未通过或证据已变化，内容未发布")
        task.review_result_json = {"review": progress["media_review"], "selection": progress["media_selection"]}
    row = await entries_service.create_post(
        db,
        task.user_id,
        activity_date=task.activity_date,
        title=title,
        body=body,
        content_type=content_type.value,
        media_url=media_url,
        audio_url=audio_url,
        context=context.model_dump(mode="json"),
    )
    task.post_id = row.id
    task.state = "succeeded"
    task.outcome = "partial" if partial else "published"
    task.stage = "complete"
    await finish_media_operation(db, task.user_id, f"post:{task.source_key}:publish", {"post_id": str(row.id)})
    await notify_post_published(db, row)
    emit_ws_event(
        db,
        user_id=row.user_id,
        event_type="companion.post.created",
        payload=response_for_post(row).model_dump(mode="json"),
    )
    await db.commit()
    return row


def entry_error(exc: EntryError) -> PostError:
    return PostNotFoundError(str(exc)) if isinstance(exc, EntryNotFoundError) else PostError(str(exc))


async def create_comment(db: AsyncSession, user_id: int, post_id: int, content: str) -> CommentView:
    try:
        result = await entries_service.create_user_comment(db, user_id, post_id, content)
        await db.commit()
        return result
    except EntryError as exc:
        raise entry_error(exc) from exc


async def delete_comment(db: AsyncSession, user_id: int, post_id: int, comment_id: int) -> None:
    try:
        await entries_service.delete_user_comment(db, user_id, post_id, comment_id)
        await db.commit()
    except EntryError as exc:
        raise entry_error(exc) from exc


async def retry_reply(db: AsyncSession, user_id: int, post_id: int, comment_id: int) -> CommentView:
    try:
        result = await entries_service.retry_comment_reply(db, user_id, post_id, comment_id)
        await db.commit()
        return result
    except EntryError as exc:
        raise entry_error(exc) from exc


async def collect_post_interactions(
    db: AsyncSession, user_id: int, *, local_date: date, user_timezone: str, utc_start: datetime, utc_end: datetime
) -> PostInteractions:
    rows = await entries_queries.interaction_entries(db, user_id, local_date, utc_start, utc_end)
    comments = await entries_queries.comments_for_entries(db, user_id, [row.id for row in rows], before=utc_end)
    threads = [
        {
            "post_id": m.id,
            "title": m.title,
            "body": m.body,
            "content_type": m.content_type,
            "published_at": format_local_iso(m.published_at, user_timezone),
            "activity_date": m.activity_date.isoformat(),
            "published_for_day": m.activity_date == local_date,
            "media_context": PostContext.model_validate(m.context_json).model_dump(exclude={"voice_id"}),
            "comments": [
                {
                    "id": c.id,
                    "reply_to_comment_id": c.reply_to_comment_id,
                    "role": c.role,
                    "content": c.content,
                    "created_at": format_local_iso(c.created_at, user_timezone),
                    "in_window": utc_start <= ensure_utc(c.created_at) < utc_end,
                }
                for c in comments.get(m.id, [])
            ],
        }
        for m in rows
    ]
    return PostInteractions(threads, [row.id for row in rows if row.activity_date == local_date])
