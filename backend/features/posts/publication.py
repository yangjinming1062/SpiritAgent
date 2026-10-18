"""独立主动创作、媒体制作与可恢复发布任务。"""

import asyncio
from datetime import date, timedelta
from typing import Any, Literal, get_args
from weakref import WeakValueDictionary

from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.prompt_engineer import LlmJsonValidationError, call_llm_json
from infrastructure.ai.providers.http import ProviderResultUnknownError
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import resolve_user_llm_config
from infrastructure.assets.asset_store import (
    dated_asset_directory,
    resolve_companion_asset_path,
    save_companion_asset_async,
    signed_companion_asset_url,
)
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import format_local_iso, resolve_language, resolve_prompt_text, utc_now
from runtime.background import TaskBag
from runtime.user_maintenance import is_user_in_maintenance, track_user_task
from sqlalchemy import or_, select, update
from sqlalchemy.orm import raiseload

from features.accounts.locking import lock_account
from features.accounts.queries import user_is_active
from features.appearance.character_card import character_snapshot_is_current, render_character_identity
from features.appearance.visual_identity import (
    apply_outfit_override,
    build_plain_image_prompt,
    build_plain_video_prompt,
    build_self_image_prompt,
    build_self_video_prompt,
    load_self_visual_context,
    optional_outfit_image_reference,
    self_video_references,
)
from features.assets.lifecycle import cleanup_user_assets, enqueue_asset_cleanup
from features.companion.narrative_prompts import NARRATIVE_REVISION_TEXTS
from features.companion.narrative_quality import NarrativeQualityError, review_narrative
from features.companion.queries import complete_user_ids
from features.configuration.settings.timezone import resolve_user_timezone
from features.configuration.settings.values import get_user_setting, load_user_settings
from features.media.character_images import generate_character_images
from features.media.contracts import VideoJobSnapshot
from features.media.identity_contracts import CharacterCardSnapshot
from features.media.image_generation import ImageGenerationError, generate_images
from features.media.queries import get_job, get_video_jobs
from features.media.speech import synthesize_speech
from features.media.video_jobs import (
    cleanup_user_media,
    discard_video_job,
    post_video_asset,
    post_video_ready,
    prepare_video_job,
    query_post_video_job,
    select_video_resolution,
    submit_video_job,
)
from features.posts.context import load_post_prompt_context, post_prompt_entry
from features.posts.models import CompanionPost, PostContentType, PostPublication
from features.posts.output_contracts import POST_DECISION, parse_post_decision
from features.posts.prompt_contract import render_post_instructions
from features.posts.prompts import POST_DECISION_REPAIR_TEXTS, POST_PUBLISH_INSTRUCTIONS
from features.posts.schemas import (
    PostContext,
    PostPlan,
    PostPublicationRecovery,
    PostPublicationRecoveryList,
    PostPublicationResult,
)
from features.posts.store import (
    PostBlockedError,
    PostError,
    PostNotFoundError,
    commit_publication,
    publication_quota_remaining,
    reserve_publication,
    response_for_publication,
)
from features.scenes.queries import get_scene_state, scene_environment

logger = get_logger(__name__)
_BG = TaskBag("posts.publication")
_TASKS: dict[str, asyncio.Task[None]] = {}
_TERMINAL = {
    "published",
    "partial",
    "failed",
    "blocked",
    "result_unknown",
    "review_pending",
    "declined",
    "discarded",
}
_RECOVERABLE_VIDEO_STATUSES = ("result_unknown", "review_pending")
_RECOVERY_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_POSTS_SWITCH = "companion.posts_enabled"
# 视频分辨率按此顺序取供应商链能接单的第一档：768P 为默认档，720P 先于 1080P，因为部分供应商模型最高到 720p；动态视频刻意不含 512P/480P 低清档。
_VIDEO_RESOLUTIONS = ("768P", "720P", "1080P")
# 动态计划允许的视频时长。
_VIDEO_DURATIONS: tuple[int, ...] = get_args(PostPlan.model_fields["duration"].annotation)


class _VideoUnsupportedError(PostError):
    """供应商链不支持本次视频的参考模式、时长或分辨率，提交前阻止。"""


class _VideoReviewPendingError(PostError):
    """已取得的原视频未通过自动身份交付准入，保留供用户确认。"""


async def available_types(user_id: int) -> list[str]:
    async with SESSION_LOCAL() as db:
        if not await user_is_active(db, user_id):
            return []
        enabled = await get_user_setting(db, user_id, _POSTS_SWITCH)
        if enabled is not None and enabled is not True:
            return []
        types = ["text"]
        for content_type, service_type in (("image", "image_gen"), ("video", "video_gen"), ("audio", "tts")):
            if await resolve_provider_chain(
                db,
                user_id,
                service_type,
                task=AiTaskKey.posts,
            ):
                types.append(content_type)
    if "video" in types and not await _video_supported(user_id):
        types.remove("video")
    return types


async def _video_supported(user_id: int) -> bool:
    """供应商链对动态允许的时长至少有一档分辨率能接单；供应商配置无法解析时按不可用处理。"""
    try:
        for duration in _VIDEO_DURATIONS:
            if await select_video_resolution(
                user_id,
                reference_images=False,
                duration=duration,
                preferred=_VIDEO_RESOLUTIONS,
                task=AiTaskKey.posts,
            ):
                return True
    except Exception:
        logger.warning("Post video capability check failed", extra={"user_id": user_id}, exc_info=True)
    return False


async def _creation_input(user_id: int) -> dict:
    ctx = await load_post_prompt_context(user_id)
    types = await available_types(user_id)
    if not types:
        raise PostBlockedError("动态自主发布已关闭")
    async with SESSION_LOCAL() as db:
        recent = (
            (
                await db.execute(
                    select(CompanionPost)
                    .options(raiseload(CompanionPost.comments))
                    .where(
                        CompanionPost.user_id == user_id,
                        CompanionPost.published_at >= ctx.now - timedelta(days=3),
                        CompanionPost.published_at <= ctx.now,
                    )
                    .order_by(CompanionPost.published_at, CompanionPost.id),
                )
            )
            .scalars()
            .all()
        )
        environment = scene_environment(await get_scene_state(db, user_id))
    return {
        **ctx.payload,
        "environment": environment,
        "available_types": types,
        "recent_posts": [post_prompt_entry(post, ctx) for post in recent],
    }


async def compose_plan(user_id: int, payload: dict) -> PostPlan | None:
    async with SESSION_LOCAL() as db:
        config = await resolve_user_llm_config(db, user_id, AiTaskKey.posts)
    if not config.is_configured:
        raise PostError("模型配置不可用")
    language = payload["output_language"]
    instructions = render_post_instructions(POST_PUBLISH_INSTRUCTIONS, language)
    creation_payload = payload

    def parse_output(value: Any) -> PostPlan | None:
        plan = parse_post_decision(value)
        if plan is not None and plan.content_type.value not in payload["available_types"]:
            raise LlmJsonValidationError("unavailable_post_type", fields=("content_type",))
        return plan

    for revision in range(2):
        plan = await call_llm_json(
            config,
            instructions,
            creation_payload,
            schema=POST_DECISION.json_schema(),
            schema_name="post_decision",
            parse_output=parse_output,
            repair_prompt=resolve_prompt_text(POST_DECISION_REPAIR_TEXTS, language),
        )
        if plan is None:
            return None
        plan = _drop_inapplicable_fields(user_id, plan, payload["available_types"])
        review = await review_narrative(
            config,
            payload,
            {
                field: value
                for field in ("publication_intent", "title", "body", "text", "narration")
                if (value := getattr(plan, field))
            },
            language,
        )
        if not review.issues:
            return plan
        if revision:
            raise NarrativeQualityError(review)
        creation_payload = {
            **payload,
            "narrative_revision": {
                "draft": {"post": True, "plan": plan.model_dump(mode="json")},
                "issues": review.model_dump()["issues"],
            },
        }
        instructions += resolve_prompt_text(NARRATIVE_REVISION_TEXTS, language)
    raise AssertionError("unreachable narrative revision state")


def _drop_inapplicable_fields(user_id: int, plan: PostPlan, types: list[str]) -> PostPlan:
    """丢弃模型多给的、不适用于所选类型或当前能力的字段，不因此拒绝整条计划。"""
    is_media = plan.content_type in (PostContentType.IMAGE, PostContentType.VIDEO)
    dropped: dict[str, Any] = {}
    if plan.narration and not (is_media and "audio" in types):
        dropped["narration"] = ""
    if plan.text and plan.content_type != PostContentType.AUDIO:
        dropped["text"] = ""
    if plan.prompt and not is_media:
        dropped["prompt"] = ""
    if plan.depicts_self and not is_media:
        dropped["depicts_self"] = False
    if not dropped:
        return plan
    logger.warning("Post plan fields dropped", extra={"user_id": user_id, "fields": sorted(dropped)})
    return plan.model_copy(update=dropped)


async def request_publication(user_id: int) -> PostPublicationResult:
    if is_user_in_maintenance(user_id):
        raise PostError("账户正在维护，暂不能发布")
    if not await available_types(user_id):
        raise PostBlockedError("动态自主发布已关闭")
    async with SESSION_LOCAL() as db:
        timezone = await resolve_user_timezone(db, user_id)
        row = await reserve_publication(
            db,
            user_id,
            activity_date=date.fromisoformat(
                (format_local_iso(utc_now(), timezone) or "")[:10],
            ),
        )
    schedule_publication(row.id, user_id)
    return response_for_publication(row)


def schedule_publication(task_id: str, user_id: int) -> None:
    if task_id in _TASKS and not _TASKS[task_id].done():
        return
    task = asyncio.create_task(_run_publication(task_id), name=f"post.publish.{task_id}")
    _TASKS[task_id] = task
    task.add_done_callback(lambda done: _TASKS.pop(task_id, None) if _TASKS.get(task_id) is done else None)
    _BG.add(task)
    track_user_task(user_id, task)


def _owned_assets(row: PostPublication) -> list[str]:
    """任务自己保存的图片、语音与旁白；视频动态的主媒体是视频任务的成品，由该任务管理。"""
    paths = [row.progress_json.get("audio_url")]
    if row.video_job_id is None:
        paths.append(row.progress_json.get("media_url"))
    return [path for path in paths if path]


async def _save(
    task_id: str,
    *,
    phase: str | None = None,
    status: str | None = None,
    error: str | None = None,
    plan: PostPlan | None = None,
    request: dict | None = None,
    **progress: Any,
) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(PostPublication).where(PostPublication.id == task_id).with_for_update())
        if row is None:
            raise PostError("发布任务已不存在")
        if row.post_id is not None or row.status in _TERMINAL:
            return
        if phase is not None:
            row.phase = phase
        if status is not None:
            row.status = status
        row.error = error
        if plan is not None:
            row.plan_json = plan.model_dump(mode="json")
        if request is not None:
            row.request_json = request
        row.progress_json = {**row.progress_json, **progress}
        # 阻止或失败的任务不会产生动态（已有动态时上方已返回），其保存的资产随之删除；结果未知的任务保留已有产物。
        discarded = _owned_assets(row) if status in ("blocked", "failed") else []
        await enqueue_asset_cleanup(db, row.user_id, discarded)
        user_id = row.user_id
        await db.commit()
    if discarded:
        await cleanup_user_assets(user_id)


async def _voice(task_id: str, user_id: int, text: str, *, phase: str, directory: str) -> tuple[str, str]:
    async with SESSION_LOCAL() as db:
        if user_id not in await complete_user_ids(db, (user_id,)):
            raise PostError("伙伴资料尚未就绪")
        settings = await load_user_settings(db, user_id, ("companion.voice_id", "language"))
    await _save(task_id, phase=phase)
    result = await synthesize_speech(
        user_id,
        text,
        settings.get("companion.voice_id") or "",
        resolve_language(settings.get("language")),
        task=AiTaskKey.posts,
    )
    mime = result.mime.lower()
    ext = "wav" if "wav" in mime else "ogg" if "ogg" in mime else "m4a" if "mp4" in mime or "m4a" in mime else "mp3"
    path = await save_companion_asset_async(
        result.audio,
        user_id=user_id,
        label="post_voice",
        ext=ext,
        directory=directory,
    )
    return path, result.voice or ""


async def _generate_media(row: PostPublication, plan: PostPlan) -> dict:
    progress = dict(row.progress_json)
    directory = progress.get("storage_directory")
    if not isinstance(directory, str) or not directory:
        async with SESSION_LOCAL() as db:
            directory = dated_asset_directory(row.created_at, await resolve_user_timezone(db, row.user_id) or "UTC")
        progress["storage_directory"] = directory
        await _save(row.id, **progress)
    if plan.content_type == PostContentType.TEXT or (
        progress.get("media_url") and plan.content_type != PostContentType.VIDEO
    ):
        return progress
    if plan.content_type == PostContentType.VIDEO and progress.get("media_url") and row.video_job_id is None:
        raise PostError("原视频任务已不可用，未重新制作")
    identity = None
    visual = await load_self_visual_context(row.user_id) if plan.depicts_self and row.video_job_id is None else None
    if visual is not None:
        identity = visual.identity
        progress["identity"] = identity.model_dump(mode="json")
    if plan.content_type == PostContentType.IMAGE:
        await _save(row.id, phase="image_submitting", **progress)
        if visual is not None:
            visual_plan = apply_outfit_override(visual, None)
            outfit = optional_outfit_image_reference(visual_plan)
            urls = await generate_character_images(
                build_self_image_prompt(visual_plan, plan.prompt, has_outfit_reference=bool(outfit)),
                size=plan.size,
                user_id=row.user_id,
                storage_directory=directory,
                reference_image=visual.identity_image,
                identity_reference=visual.identity_image,
                secondary_reference_image=outfit,
                identity_reference_mode="primary"
                if visual.identity_reference_role == "fullbody"
                else "portrait_and_outfit",
                goal_review="required",
                identity_text=render_character_identity(identity),
                task=AiTaskKey.posts,
            )
        else:
            result = await generate_images(
                build_plain_image_prompt(plan.prompt),
                size=plan.size,
                user_id=row.user_id,
                persist_user_assets=True,
                storage_directory=directory,
                task=AiTaskKey.posts,
            )
            urls = result.urls
        if not urls:
            raise PostError("图片生成未取得可用产物")
        progress["media_url"] = urls[0]
    elif plan.content_type == PostContentType.AUDIO:
        progress["media_url"], progress["voice_id"] = await _voice(
            row.id,
            row.user_id,
            plan.text,
            phase="voice_submitting",
            directory=directory,
        )
    else:
        job_id = row.video_job_id
        if job_id is None:
            references = ()
            if visual is not None:
                visual_plan = apply_outfit_override(visual, None)
                references = self_video_references(visual_plan)
                prompt = build_self_video_prompt(visual_plan, plan.prompt, has_outfit_reference=len(references) > 1)
            else:
                prompt = build_plain_video_prompt(plan.prompt)
            resolution = await select_video_resolution(
                row.user_id,
                reference_images=bool(references),
                duration=plan.duration,
                preferred=_VIDEO_RESOLUTIONS,
                task=AiTaskKey.posts,
            )
            if resolution is None:
                raise _VideoUnsupportedError
            await _save(row.id, phase="video_submitting", **progress)
            async with SESSION_LOCAL() as db:
                publication = await db.get(PostPublication, row.id, with_for_update=True)
                if publication is None or publication.status in _TERMINAL:
                    raise PostError("发布任务已不可执行")
                job = await prepare_video_job(
                    db,
                    user_id=row.user_id,
                    session_id=None,
                    asset_directory=directory,
                    prompt=prompt,
                    duration=plan.duration,
                    resolution=resolution,
                    reference_images=references,
                    aspect_ratio=plan.aspect_ratio,
                    identity_reference_path=visual.fullbody_path if visual else None,
                    identity=identity,
                    identity_reference_role=visual.identity_reference_role if visual else "portrait",
                    task=AiTaskKey.posts,
                )
                publication.video_job_id = job.id
                publication.phase = "video_waiting"
                publication.progress_json = {**publication.progress_json, **progress}
                await db.commit()
            job_id = job.id
            await submit_video_job(row.user_id, job_id)
        while True:
            async with SESSION_LOCAL() as db:
                job = await get_job(db, job_id, row.user_id)
            if job is None or job.user_id != row.user_id or job.status == "failed":
                raise PostError("视频生成失败")
            if job.status == "result_unknown":
                raise ProviderResultUnknownError("POST", "")
            if job.status == "review_pending":
                raise _VideoReviewPendingError(job.error_message or "视频身份尚待确认，未自动发布")
            if job.status == "succeeded":
                progress["media_url"] = job.video_url
                break
            await asyncio.sleep(3)
    await _save(row.id, phase="media_ready", **progress)
    return progress


async def _run_publication(task_id: str) -> None:
    try:
        async with SESSION_LOCAL() as db:
            row = await db.get(PostPublication, task_id)
        if row is None or row.status in _TERMINAL:
            return
        if row.phase == "narration_submitting" and row.progress_json.get("media_url"):
            row.progress_json = {**row.progress_json, "narration_failed": True}
            await _save(task_id, phase="media_ready", **row.progress_json)
        elif row.phase.endswith("_submitting"):
            await _save(task_id, status="result_unknown", error="制作请求的结果尚未确认，未重复提交")
            return
        if is_user_in_maintenance(row.user_id):
            return
        if row.plan_json is None:
            if not await available_types(row.user_id):
                await _save(task_id, status="blocked", error="发布开关已关闭")
                return
            payload = await _creation_input(row.user_id)
            await _save(task_id, status="running", phase="planning", request=payload)
            plan = await compose_plan(row.user_id, payload)
            if plan is None:
                await _save(task_id, status="declined", phase="complete")
                return
            await _save(task_id, plan=plan, phase="planned")
        else:
            plan = PostPlan.model_validate(row.plan_json)
        if plan.content_type.value not in await available_types(row.user_id):
            await _save(task_id, status="blocked", error="发布开关或供应商能力不可用")
            return
        await _save(task_id, status="running")
        progress = await _generate_media(row, plan)
        if (
            plan.narration
            and plan.content_type in (PostContentType.IMAGE, PostContentType.VIDEO)
            and not progress.get("audio_url")
            and not progress.get("narration_failed")
        ):
            try:
                voice_types = await available_types(row.user_id)
                if "audio" not in voice_types:
                    raise PostError("旁白不可用")
                progress["audio_url"], progress["voice_id"] = await _voice(
                    task_id,
                    row.user_id,
                    plan.narration,
                    phase="narration_submitting",
                    directory=progress["storage_directory"],
                )
            except Exception:
                logger.warning("Post narration failed", extra={"publication_id": task_id}, exc_info=True)
                progress["narration_failed"] = True
            await _save(task_id, phase="media_ready", **progress)
        if progress.get("identity"):
            async with SESSION_LOCAL() as db:
                current = await character_snapshot_is_current(
                    db,
                    row.user_id,
                    CharacterCardSnapshot.model_validate(progress["identity"]),
                )
            if not current:
                await _save(task_id, status="blocked", error="伙伴外形已更新，本次旧参考作品未发布")
                return
        if plan.content_type.value not in await available_types(row.user_id):
            await _save(task_id, status="blocked", error="发布开关已关闭")
            return
        async with SESSION_LOCAL() as db:
            await commit_publication(
                db,
                task_id,
                title=plan.title,
                body=plan.body,
                content_type=plan.content_type,
                media_url=progress.get("media_url"),
                audio_url=progress.get("audio_url"),
                context=PostContext(
                    publication_intent=plan.publication_intent,
                    creation_intent=plan.prompt,
                    transcript=plan.text,
                    narration=plan.narration if progress.get("audio_url") else "",
                    voice_id=progress.get("voice_id", ""),
                ),
                partial=bool(progress.get("narration_failed")),
            )
    except asyncio.CancelledError:
        raise
    except _VideoUnsupportedError:
        await _save(task_id, status="blocked", error="视频供应商不支持本次视频要求")
    except _VideoReviewPendingError as exc:
        await _save(task_id, status="review_pending", error=str(exc))
    except PostBlockedError as exc:
        await _save(task_id, status="blocked", error=str(exc))
    except ProviderResultUnknownError:
        await _save(task_id, status="result_unknown", error="制作请求结果未知，未重复提交")
    except ImageGenerationError as exc:
        await _save(
            task_id,
            status="result_unknown" if exc.result_unknown else "failed",
            error="图片制作结果未知，未重复提交" if exc.result_unknown else "图片制作失败",
        )
    except Exception:
        logger.exception("Post publication failed", extra={"publication_id": task_id})
        await _save(task_id, status="failed", error="动态制作失败，内容未发布")


async def resume_publications() -> None:
    async with SESSION_LOCAL() as db:
        rows = list(
            (await db.scalars(select(PostPublication).where(PostPublication.status.in_(("queued", "running"))))).all(),
        )
    for row in rows:
        schedule_publication(row.id, row.user_id)


async def drain_publications() -> None:
    await _BG.drain()


def _recovery_lock(task_id: str) -> asyncio.Lock:
    return _RECOVERY_LOCKS.setdefault(task_id, asyncio.Lock())


def _video_job_id(row: PostPublication) -> int | None:
    return row.video_job_id


def _recovery_response(row: PostPublication, job: VideoJobSnapshot | None) -> PostPublicationRecovery:
    video_status: Literal["pending", "ready", "failed", "unknown", "discarded"] = "unknown"
    media_url = None
    can_adopt = False
    if row.status == "discarded" or (job is not None and job.status == "discarded"):
        video_status = "discarded"
    elif job is not None and job.status == "failed":
        video_status = "failed"
    elif job is not None:
        path = job.video_url or job.candidate_video_url
        parsed = parse_companion_asset_path(path)
        if parsed is not None and parsed[0] == row.user_id and resolve_companion_asset_path(*parsed) is not None:
            video_status = "ready"
            media_url = signed_companion_asset_url(path)
            can_adopt = row.status in _RECOVERABLE_VIDEO_STATUSES and post_video_ready(job)
        elif job.status != "result_unknown":
            video_status = "pending"
    return PostPublicationRecovery(
        **response_for_publication(row).model_dump(),
        title=(row.plan_json or {}).get("title", "视频动态"),
        video_status=video_status,
        media_url=media_url,
        can_adopt=can_adopt,
        can_discard=row.post_id is None
        and (
            row.status in _RECOVERABLE_VIDEO_STATUSES
            or (row.status == "discarded" and bool(row.progress_json.get("discard_cleanup_pending")))
        ),
    )


async def publication_recovery(user_id: int, task_id: str, *, query: bool = False) -> PostPublicationRecovery:
    async with _recovery_lock(task_id):
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(PostPublication).where(PostPublication.id == task_id, PostPublication.user_id == user_id),
            )
        if row is None:
            raise PostNotFoundError("找不到发布任务")
        job_id = _video_job_id(row)
        if query and row.status in _RECOVERABLE_VIDEO_STATUSES and job_id is not None:
            try:
                job = await query_post_video_job(user_id, job_id)
            except Exception as exc:
                logger.warning("original post video query failed", extra={"publication_id": task_id}, exc_info=True)
                raise PostError("原视频任务暂时无法查询，请稍后再试") from exc
        else:
            async with SESSION_LOCAL() as db:
                job = await get_job(db, job_id, user_id) if job_id else None
        return _recovery_response(row, job)


async def list_publication_recoveries(user_id: int, *, limit: int = 50, offset: int = 0) -> PostPublicationRecoveryList:
    async with SESSION_LOCAL() as db:
        rows = list(
            (
                await db.scalars(
                    select(PostPublication)
                    .where(
                        PostPublication.user_id == user_id,
                        or_(
                            PostPublication.status.in_(_RECOVERABLE_VIDEO_STATUSES),
                            (PostPublication.status == "discarded")
                            & PostPublication.progress_json["discard_cleanup_pending"].as_boolean().is_(True),
                        ),
                        PostPublication.plan_json["content_type"].as_string() == "video",
                    )
                    .order_by(PostPublication.created_at.desc(), PostPublication.id.desc())
                    .offset(offset)
                    .limit(limit + 1),
                )
            ).all(),
        )
        job_ids = [job_id for row in rows[:limit] if (job_id := _video_job_id(row)) is not None]
        jobs = await get_video_jobs(db, user_id, job_ids)
    return PostPublicationRecoveryList(
        items=[_recovery_response(row, jobs.get(_video_job_id(row))) for row in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )


async def adopt_publication_video(user_id: int, task_id: str) -> PostPublicationResult:
    """明确采纳现有视频：原任务幂等，按采纳时政策、身份和额度发布，不重新制作旁白。"""
    if is_user_in_maintenance(user_id):
        raise PostError("账户正在维护，暂不能采纳")
    async with _recovery_lock(task_id), SESSION_LOCAL() as db:
        await lock_account(db, user_id)
        row = await db.scalar(
            select(PostPublication)
            .where(PostPublication.id == task_id, PostPublication.user_id == user_id)
            .with_for_update(),
        )
        if row is None:
            raise PostNotFoundError("找不到发布任务")
        if row.post_id is not None:
            return response_for_publication(row)
        if row.status not in _RECOVERABLE_VIDEO_STATUSES or row.plan_json is None:
            raise PostError("只有待核对的视频动态可以采纳")
        if not await user_is_active(db, user_id) or user_id not in await complete_user_ids(db, (user_id,)):
            raise PostBlockedError("账户或伙伴资料不可用")
        enabled = await get_user_setting(db, user_id, _POSTS_SWITCH)
        if enabled is not None and enabled is not True:
            raise PostBlockedError("动态自主发布已关闭")
        remaining = await publication_quota_remaining(db, user_id, exclude_publication_id=row.id)
        if remaining <= 0:
            raise PostBlockedError("最近24小时的动态发布额度已用完")
        plan = PostPlan.model_validate(row.plan_json)
        job_id = _video_job_id(row)
        if plan.content_type != PostContentType.VIDEO or job_id is None:
            raise PostError("本任务没有可查询的原视频成品")
        if row.progress_json.get("identity") and not await character_snapshot_is_current(
            db,
            user_id,
            CharacterCardSnapshot.model_validate(row.progress_json["identity"]),
        ):
            raise PostBlockedError("伙伴外形已更新，不能采纳旧参考视频")
        try:
            path = await post_video_asset(db, user_id, job_id)
        except ValueError as exc:
            raise PostError(str(exc)) from exc
        row.progress_json = {**row.progress_json, "media_url": path}
        # 发布入口会重读任务；autoflush=False，先保存本事务的采纳进度。
        await db.flush()
        await commit_publication(
            db,
            task_id,
            title=plan.title,
            body=plan.body,
            content_type=plan.content_type,
            media_url=path,
            audio_url=row.progress_json.get("audio_url"),
            context=PostContext(
                publication_intent=plan.publication_intent,
                creation_intent=plan.prompt,
                narration=plan.narration if row.progress_json.get("audio_url") else "",
                voice_id=row.progress_json.get("voice_id", ""),
            ),
            partial=bool(plan.narration and not row.progress_json.get("audio_url")),
        )
        return response_for_publication(row)


async def discard_publication_video(user_id: int, task_id: str) -> PostPublicationResult:
    async with _recovery_lock(task_id):
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(PostPublication)
                .where(PostPublication.id == task_id, PostPublication.user_id == user_id)
                .with_for_update(),
            )
            if row is None:
                raise PostNotFoundError("找不到发布任务")
            if row.status not in (*_RECOVERABLE_VIDEO_STATUSES, "discarded") or row.post_id is not None:
                raise PostError("只有尚未发布的待核对视频动态可以放弃")
            if row.status == "discarded" and not row.progress_json.get("discard_cleanup_pending"):
                return response_for_publication(row)
            job_id = _video_job_id(row)
            row.status = "discarded"
            row.phase = "complete"
            row.error = "已放弃本次动态，不会自动重新制作或发布"
            row.progress_json = {**row.progress_json, "discard_cleanup_pending": True}
            if job_id is not None:
                shared = await db.scalar(
                    select(PostPublication.id)
                    .where(
                        PostPublication.user_id == user_id,
                        PostPublication.id != task_id,
                        PostPublication.status != "discarded",
                        PostPublication.video_job_id == job_id,
                    )
                    .limit(1),
                )
                if shared is None:
                    try:
                        await discard_video_job(db, user_id, job_id)
                    except ValueError as exc:
                        raise PostError(str(exc)) from exc
            await enqueue_asset_cleanup(db, user_id, _owned_assets(row))
            await db.commit()
        if job_id is not None:
            await cleanup_user_media(user_id)
        else:
            await cleanup_user_assets(user_id)
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(PostPublication)
                .where(PostPublication.id == task_id, PostPublication.user_id == user_id)
                .with_for_update(),
            )
            if row is None:
                raise PostNotFoundError("发布任务已不存在")
            row.progress_json = {
                key: value for key, value in row.progress_json.items() if key != "discard_cleanup_pending"
            }
            await db.commit()
        return response_for_publication(row)


async def gc_autonomous_publications() -> tuple[int, int]:
    """完整资料保留七天、精简记录九十天；未知和在途媒体职责不删除。"""
    compacted = deleted = 0
    cutoff = utc_now() - timedelta(days=7)
    remove_before = utc_now() - timedelta(days=90)
    cursor: str | None = None
    while True:
        async with SESSION_LOCAL() as db:
            statement = (
                select(PostPublication)
                .where(
                    PostPublication.status.in_(_TERMINAL - set(_RECOVERABLE_VIDEO_STATUSES)),
                    PostPublication.updated_at < cutoff,
                )
                .order_by(PostPublication.id)
                .limit(200)
                .with_for_update(skip_locked=True)
            )
            if cursor:
                statement = statement.where(PostPublication.id > cursor)
            rows = list((await db.scalars(statement)).all())
            if not rows:
                break
            cursor = rows[-1].id
            candidates: list[PostPublication] = []
            for row in rows:
                if (
                    row.progress_json.get("discard_cleanup_pending")
                    or is_user_in_maintenance(row.user_id)
                    or ((task := _TASKS.get(row.id)) is not None and not task.done())
                ):
                    continue
                candidates.append(row)
            if not candidates:
                continue
            jobs: dict[int, VideoJobSnapshot] = {}
            for user_id in {row.user_id for row in candidates}:
                job_ids = [
                    row.video_job_id for row in candidates if row.user_id == user_id and row.video_job_id is not None
                ]
                jobs.update(await get_video_jobs(db, user_id, job_ids))
            for row in candidates:
                job_id = _video_job_id(row)
                if job_id is not None:
                    job = jobs.get(job_id)
                    if job is not None and job.status not in ("succeeded", "failed", "discarded"):
                        continue
                keep = {
                    key: value
                    for key, value in row.progress_json.items()
                    if key in ("media_url", "audio_url", "voice_id", "narration_failed")
                }
                if row.updated_at < remove_before and row.post_id is None and row.video_job_id is None and not keep:
                    await db.delete(row)
                    deleted += 1
                elif row.request_json or row.plan_json is not None or row.progress_json != keep:
                    await db.execute(
                        update(PostPublication)
                        .where(PostPublication.id == row.id)
                        .values(
                            request_json={},
                            plan_json=None,
                            progress_json=keep,
                            updated_at=row.updated_at,
                        ),
                    )
                    compacted += 1
            await db.commit()
    return compacted, deleted
