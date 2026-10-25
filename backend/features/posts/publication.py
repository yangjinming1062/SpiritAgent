"""独立主动创作、媒体制作与可恢复发布任务。"""

import asyncio
from datetime import timedelta
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
from infrastructure.domain_locks import lock_domain
from infrastructure.logger import get_logger
from infrastructure.pagination import decode_timestamp_cursor, encode_timestamp_cursor
from infrastructure.text import local_date, resolve_language, resolve_prompt_text, utc_now
from runtime.background import TaskBag
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from features.accounts.queries import user_is_active
from features.appearance.character_card import character_snapshot_is_current
from features.appearance.visual_identity import (
    build_plain_image_prompt,
    build_plain_video_prompt,
    build_self_image_inputs,
    build_self_video_inputs,
    load_self_visual_context,
)
from features.assets.lifecycle import cleanup_user_assets, enqueue_asset_cleanup
from features.companion.narrative_prompts import NARRATIVE_REVISION_TEXTS
from features.companion.narrative_quality import NarrativeQualityError, review_narrative
from features.companion.queries import complete_user_ids
from features.configuration.settings.timezone import resolve_user_timezone
from features.configuration.settings.values import get_user_setting_bool, load_user_settings
from features.entries.contracts import PostContentType
from features.entries.queries import recent_posts
from features.jobs.service import bind_job_assets
from features.media.character_images import generate_character_images
from features.media.contracts import VideoJobSnapshot
from features.media.identity_contracts import CharacterCardSnapshot, CharacterReferenceRole
from features.media.image_generation import ImageGenerationError, generate_images
from features.media.queries import get_job, get_video_jobs
from features.media.speech import synthesize_speech
from features.media.video_jobs import (
    cleanup_user_media,
    discard_video_job,
    post_video_asset,
    prepare_video_job,
    query_post_video_job,
    select_video_resolution,
    submit_video_job,
)
from features.posts.context import load_post_prompt_context, post_prompt_entry
from features.posts.output_contracts import POST_DECISION, parse_post_decision
from features.posts.ports import notify_post_published
from features.posts.prompt_contract import render_post_instructions
from features.posts.prompts import POST_DECISION_REPAIR_TEXTS, POST_PUBLISH_INSTRUCTIONS
from features.posts.publication_models import PostPublication
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
from features.visuals.queries import prompt_presentation_snapshot

logger = get_logger(__name__)
_BG = TaskBag("posts.publication")
_TASKS: dict[int, asyncio.Task[None]] = {}
_TERMINAL = {"succeeded", "failed", "cancelled"}
_RECOVERABLE_VIDEO_STATUSES = ("unknown",)
_RECOVERY_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
_POSTS_SWITCH = "companion.posts_enabled"
# 视频分辨率按此顺序取供应商链能接单的第一档：768P 为默认档，720P 先于 1080P，因为部分供应商模型最高到 720p；动态视频刻意不含 512P/480P 低清档。
_VIDEO_RESOLUTIONS = ("768P", "720P", "1080P")
# 动态计划允许的视频时长。
_VIDEO_DURATIONS: tuple[int, ...] = get_args(PostPlan.model_fields["duration"].annotation)


class _VideoUnsupportedError(PostError):
    """供应商链不支持本次视频的参考模式、时长或分辨率，提交前阻止。"""


class _VideoQualityError(PostError):
    """已取得的原视频未通过自动身份交付准入，不发布。"""


_SERVICE_TYPES = {"image": "image_gen", "video": "video_gen", "audio": "tts"}


async def _posts_enabled(db: AsyncSession, user_id: int) -> bool:
    return await get_user_setting_bool(db, user_id, _POSTS_SWITCH, default=True)


async def _posts_policy_ok(db: AsyncSession, user_id: int) -> bool:
    return await user_is_active(db, user_id) and await _posts_enabled(db, user_id)


async def _service_chain_ready(db: AsyncSession, user_id: int, content_type: str) -> bool:
    """单档服务能力：text 无条件，其余按服务链非空；video 另做分辨率试探（试探失败按不可用，链解析异常上抛）。"""
    if content_type == "text":
        return True
    if not await resolve_provider_chain(db, user_id, _SERVICE_TYPES[content_type], task=AiTaskKey.posts):
        return False
    return content_type != "video" or await _video_supported(user_id)


async def _post_type_ready(user_id: int, content_type: str) -> bool:
    """定点判定某档此刻能否制作，等价于 content_type ∈ available_types(user_id)。"""
    async with SESSION_LOCAL() as db:
        return await _posts_policy_ok(db, user_id) and await _service_chain_ready(db, user_id, content_type)


async def available_types(user_id: int) -> list[str]:
    async with SESSION_LOCAL() as db:
        if not await _posts_policy_ok(db, user_id):
            return []
        types = ["text"]
        for content_type in _SERVICE_TYPES:
            if await _service_chain_ready(db, user_id, content_type):
                types.append(content_type)
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
    types = await available_types(user_id)
    if not types:
        raise PostBlockedError("发布开关已关闭")
    ctx = await load_post_prompt_context(user_id)
    async with SESSION_LOCAL() as db:
        recent = await recent_posts(db, user_id, since=ctx.now - timedelta(days=3), until=ctx.now)
        environment = scene_environment(await get_scene_state(db, user_id))
    presentation = await prompt_presentation_snapshot(user_id)
    return {
        **ctx.payload,
        "environment": environment,
        "presentation": {
            "visual_description": presentation.visual_description,
            "outfit_description": presentation.outfit_description,
            "scene_description": presentation.scene_description,
        },
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
    async with SESSION_LOCAL() as db:
        if not await _posts_policy_ok(db, user_id):
            raise PostBlockedError("动态自主发布已关闭")
        timezone = await resolve_user_timezone(db, user_id)
        row = await reserve_publication(
            db,
            user_id,
            activity_date=local_date(utc_now(), timezone),
        )
    schedule_publication(row.id, user_id)
    return response_for_publication(row)


def schedule_publication(task_id: int, user_id: int) -> None:
    if task_id in _TASKS and not _TASKS[task_id].done():
        return
    task = asyncio.create_task(_run_publication(task_id), name=f"post.publish.{task_id}")
    _TASKS[task_id] = task
    task.add_done_callback(lambda done: _TASKS.pop(task_id, None) if _TASKS.get(task_id) is done else None)
    _BG.add(task, user_id=user_id)


def _owned_assets(row: PostPublication) -> list[str]:
    """任务自己保存的图片、语音与旁白；视频动态的主媒体是视频任务的成品，由该任务管理。"""
    paths = [(row.checkpoint or {}).get("audio_url")]
    if row.video_job_id is None:
        paths.append((row.checkpoint or {}).get("media_url"))
    return [path for path in paths if path]


async def _save(
    task_id: int,
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
        if row.post_id is not None or row.state in _TERMINAL:
            return
        previous_assets = set(_owned_assets(row))
        if phase is not None:
            row.stage = phase
        if status is not None:
            outcomes = {
                "published": "succeeded",
                "partial": "succeeded",
                "blocked": "failed",
                "declined": "cancelled",
                "discarded": "cancelled",
            }
            row.state = outcomes.get(status, "unknown" if status == "result_unknown" else status)
            row.outcome = status if status in outcomes else None
        row.error = error
        if plan is not None:
            row.plan_json = plan.model_dump(mode="json")
        if request is not None:
            row.request_json = request
        row.checkpoint = {**(row.checkpoint or {}), **progress}
        added_assets = set(_owned_assets(row)) - previous_assets
        if added_assets:
            await bind_job_assets(db, row.user_id, row.id, added_assets, "output")
        # 阻止或失败的任务不会产生动态（已有动态时上方已返回），其保存的资产随之删除；结果未知的任务保留已有产物。
        discarded = _owned_assets(row) if status in ("blocked", "failed") else []
        await enqueue_asset_cleanup(db, row.user_id, discarded)
        user_id = row.user_id
        await db.commit()
    if discarded:
        await cleanup_user_assets(user_id)


async def _voice(task_id: int, user_id: int, text: str, *, phase: str, directory: str) -> tuple[str, str]:
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
    progress = dict(row.checkpoint)
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
    visual_plan = None
    if plan.depicts_self and row.video_job_id is None:
        presentation = await prompt_presentation_snapshot(row.user_id)
        visual_plan = await load_self_visual_context(row.user_id, presentation)
    if visual_plan is not None:
        identity = visual_plan.context.identity
        progress["identity"] = identity.model_dump(mode="json")
    if plan.content_type == PostContentType.IMAGE:
        await _save(row.id, phase="image_submitting", **progress)
        if visual_plan is not None:
            inputs = build_self_image_inputs(visual_plan, plan.prompt)
            urls = await generate_character_images(
                inputs.prompt,
                size=plan.size,
                user_id=row.user_id,
                storage_directory=directory,
                reference_image=inputs.reference_image,
                identity_reference=inputs.reference_image,
                secondary_reference_image=inputs.secondary_reference_image,
                identity_reference_mode=inputs.identity_reference_mode,
                goal_review="required",
                identity_text=inputs.identity_text,
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
            references: tuple[str, ...] = ()
            identity_reference_role: CharacterReferenceRole = "portrait"
            if visual_plan is not None:
                inputs = await build_self_video_inputs(visual_plan, plan.prompt)
                prompt, references = inputs.prompt, inputs.references
                identity_reference_role = inputs.identity_reference_role
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
                if publication is None or publication.state in _TERMINAL:
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
                    identity_reference_path=visual_plan.context.fullbody_path if visual_plan else None,
                    identity=identity,
                    identity_reference_role=identity_reference_role,
                    task=AiTaskKey.posts,
                )
                publication.video_job_id = job.id
                publication.stage = "video_waiting"
                await db.commit()
            job_id = job.id
            await submit_video_job(row.user_id, job_id)
        while True:
            async with SESSION_LOCAL() as db:
                job = await get_job(db, job_id, row.user_id)
            if job is None or job.user_id != row.user_id:
                raise PostError("视频生成失败")
            if job.status == "result_unknown":
                raise ProviderResultUnknownError("POST", "")
            if job.status == "failed":
                raise _VideoQualityError(job.error_message or "视频未通过身份检查，内容未发布")
            if job.status == "succeeded":
                progress["media_url"] = job.video_url
                break
            await asyncio.sleep(3)
    await _save(row.id, phase="media_ready", **progress)
    return progress


async def _run_publication(task_id: int) -> None:
    try:
        async with SESSION_LOCAL() as db:
            row = await db.get(PostPublication, task_id)
        if row is None or row.state in _TERMINAL:
            return
        if row.stage == "narration_submitting" and (row.checkpoint or {}).get("media_url"):
            row.checkpoint = {**(row.checkpoint or {}), "narration_failed": True}
            await _save(task_id, phase="media_ready", **(row.checkpoint or {}))
        elif row.stage.endswith("_submitting"):
            await _save(task_id, status="result_unknown", error="制作请求的结果尚未确认，未重复提交")
            return
        if is_user_in_maintenance(row.user_id):
            return
        if row.plan_json is None:
            payload = await _creation_input(row.user_id)
            await _save(task_id, status="running", phase="planning", request=payload)
            plan = await compose_plan(row.user_id, payload)
            if plan is None:
                await _save(task_id, status="declined", phase="complete")
                return
            await _save(task_id, plan=plan, phase="planned")
        else:
            plan = PostPlan.model_validate(row.plan_json)
        if not await _post_type_ready(row.user_id, plan.content_type.value):
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
                if not await _post_type_ready(row.user_id, "audio"):
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
        if not await _post_type_ready(row.user_id, plan.content_type.value):
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
    except _VideoQualityError as exc:
        await _save(task_id, status="failed", error=str(exc))
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
            (await db.scalars(select(PostPublication).where(PostPublication.state.in_(("queued", "running"))))).all(),
        )
    for row in rows:
        schedule_publication(row.id, row.user_id)
    async with SESSION_LOCAL() as db:
        recoveries = list(
            await db.scalars(
                select(PostPublication).where(
                    PostPublication.state == "unknown",
                    PostPublication.video_job_id.is_not(None),
                ),
            ),
        )
    for row in recoveries:
        async with SESSION_LOCAL() as db:
            job = await get_job(db, row.video_job_id, row.user_id)
        if job is None or job.status != "succeeded":
            continue
        try:
            await publication_recovery(row.user_id, row.id, query=True)
        except PostBlockedError as exc:
            await _save(row.id, status="blocked", error=str(exc))
        except PostError:
            # 维护、额度或查询故障可能是暂时的，保留待核对状态供下次启动继续发布。
            logger.warning("post publication recovery deferred", extra={"publication_id": row.id}, exc_info=True)


async def drain_publications() -> None:
    await _BG.drain()


def _recovery_lock(task_id: int) -> asyncio.Lock:
    return _RECOVERY_LOCKS.setdefault(task_id, asyncio.Lock())


def _video_job_id(row: PostPublication) -> int | None:
    return row.video_job_id


def _recovery_response(row: PostPublication, job: VideoJobSnapshot | None) -> PostPublicationRecovery:
    video_status: Literal["pending", "ready", "failed", "unknown", "discarded"] = "unknown"
    media_url = None
    if row.outcome == "discarded" or (job is not None and job.status == "discarded"):
        video_status = "discarded"
    elif job is not None and job.status == "failed":
        video_status = "failed"
    elif job is not None:
        path = job.video_url or job.candidate_video_url
        parsed = parse_companion_asset_path(path)
        if parsed is not None and parsed[0] == row.user_id and resolve_companion_asset_path(*parsed) is not None:
            video_status = "ready"
            media_url = signed_companion_asset_url(path)
        elif job.status != "result_unknown":
            video_status = "pending"
    return PostPublicationRecovery(
        **response_for_publication(row).model_dump(),
        title=(row.plan_json or {}).get("title", "视频动态"),
        video_status=video_status,
        media_url=media_url,
        can_discard=row.post_id is None
        and (row.state in _RECOVERABLE_VIDEO_STATUSES or (row.outcome == "discarded" and row.cleanup_pending)),
    )


async def publication_recovery(user_id: int, task_id: int, *, query: bool = False) -> PostPublicationRecovery:
    async with _recovery_lock(task_id):
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(PostPublication).where(PostPublication.id == task_id, PostPublication.user_id == user_id),
            )
        if row is None:
            raise PostNotFoundError("找不到发布任务")
        job_id = _video_job_id(row)
        if query and row.state in _RECOVERABLE_VIDEO_STATUSES and job_id is not None:
            try:
                job = await query_post_video_job(user_id, job_id)
            except Exception as exc:
                logger.warning("original post video query failed", extra={"publication_id": task_id}, exc_info=True)
                raise PostError("原视频任务暂时无法查询，请稍后再试") from exc
        else:
            async with SESSION_LOCAL() as db:
                job = await get_job(db, job_id, user_id) if job_id else None
        if query and job is not None:
            if job.status == "succeeded" and row.state in _RECOVERABLE_VIDEO_STATUSES:
                await _publish_checked_video(user_id, task_id)
            elif job.status == "failed" and row.state in _RECOVERABLE_VIDEO_STATUSES:
                await _save(task_id, status="failed", error=job.error_message or "视频未通过检查，内容未发布")
            async with SESSION_LOCAL() as db:
                row = await db.scalar(
                    select(PostPublication).where(PostPublication.id == task_id, PostPublication.user_id == user_id),
                )
        return _recovery_response(row, job)


async def list_publication_recoveries(
    user_id: int, *, limit: int = 50, cursor: str | None = None
) -> PostPublicationRecoveryList:
    boundary = decode_timestamp_cursor(cursor)
    statement = (
        select(PostPublication)
        .options(
            load_only(
                PostPublication.id,
                PostPublication.user_id,
                PostPublication.state,
                PostPublication.outcome,
                PostPublication.error,
                PostPublication.post_id,
                PostPublication.video_job_id,
                PostPublication.plan_json,
                PostPublication.cleanup_pending,
                PostPublication.created_at,
                raiseload=True,
            )
        )
        .where(
            PostPublication.user_id == user_id,
            or_(
                PostPublication.state.in_(_RECOVERABLE_VIDEO_STATUSES),
                (PostPublication.outcome == "discarded") & PostPublication.cleanup_pending.is_(True),
            ),
            PostPublication.plan_json["content_type"].as_string() == "video",
        )
    )
    if boundary is not None:
        statement = statement.where(tuple_(PostPublication.created_at, PostPublication.id) < boundary)
    async with SESSION_LOCAL() as db:
        rows = list(
            await db.scalars(
                statement.order_by(PostPublication.created_at.desc(), PostPublication.id.desc()).limit(limit + 1)
            )
        )
        more = len(rows) > limit
        rows = rows[:limit]
        job_ids = [job_id for row in rows if (job_id := _video_job_id(row)) is not None]
        jobs = await get_video_jobs(db, user_id, job_ids) if job_ids else {}
        return PostPublicationRecoveryList(
            items=[_recovery_response(row, jobs.get(_video_job_id(row))) for row in rows],
            next_cursor=encode_timestamp_cursor(rows[-1].created_at, rows[-1].id) if more else None,
            has_more=more,
        )


async def _publish_checked_video(user_id: int, task_id: int) -> PostPublicationResult:
    """调用方持恢复锁；核对当前政策与检查结果后发布原视频。"""
    if is_user_in_maintenance(user_id):
        raise PostError("账户正在维护，暂不能发布")
    async with SESSION_LOCAL() as db:
        await lock_domain(db, user_id, "entries")
        row = await db.scalar(
            select(PostPublication)
            .where(PostPublication.id == task_id, PostPublication.user_id == user_id)
            .with_for_update(),
        )
        if row is None:
            raise PostNotFoundError("找不到发布任务")
        if row.post_id is not None:
            await notify_post_published(db, user_id, row.post_id)
            await db.commit()
            return response_for_publication(row)
        if row.state not in _RECOVERABLE_VIDEO_STATUSES or row.plan_json is None:
            raise PostError("只有结果待核对的视频动态可以继续发布")
        if not await user_is_active(db, user_id) or user_id not in await complete_user_ids(db, (user_id,)):
            raise PostBlockedError("账户或伙伴资料不可用")
        if not await _posts_enabled(db, user_id):
            raise PostBlockedError("动态自主发布已关闭")
        remaining = await publication_quota_remaining(db, user_id, exclude_publication_id=row.id)
        if remaining <= 0:
            raise PostBlockedError("最近24小时的动态发布额度已用完")
        plan = PostPlan.model_validate(row.plan_json)
        job_id = _video_job_id(row)
        if plan.content_type != PostContentType.VIDEO or job_id is None:
            raise PostError("本任务没有可查询的原视频成品")
        if (row.checkpoint or {}).get("identity") and not await character_snapshot_is_current(
            db,
            user_id,
            CharacterCardSnapshot.model_validate(row.checkpoint["identity"]),
        ):
            raise PostBlockedError("伙伴外形已更新，不能发布旧参考视频")
        try:
            path = await post_video_asset(db, user_id, job_id)
        except ValueError as exc:
            raise PostError(str(exc)) from exc
        row.checkpoint = {**(row.checkpoint or {}), "media_url": path}
        # 发布入口会重读任务；autoflush=False，先保存本事务的发布进度。
        await db.flush()
        await commit_publication(
            db,
            task_id,
            title=plan.title,
            body=plan.body,
            content_type=plan.content_type,
            media_url=path,
            audio_url=(row.checkpoint or {}).get("audio_url"),
            context=PostContext(
                publication_intent=plan.publication_intent,
                creation_intent=plan.prompt,
                narration=plan.narration if (row.checkpoint or {}).get("audio_url") else "",
                voice_id=(row.checkpoint or {}).get("voice_id", ""),
            ),
            partial=bool(plan.narration and not (row.checkpoint or {}).get("audio_url")),
        )
        return response_for_publication(row)


async def discard_publication_video(user_id: int, task_id: int) -> PostPublicationResult:
    async with _recovery_lock(task_id):
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(PostPublication)
                .where(PostPublication.id == task_id, PostPublication.user_id == user_id)
                .with_for_update(),
            )
            if row is None:
                raise PostNotFoundError("找不到发布任务")
            if (row.state not in _RECOVERABLE_VIDEO_STATUSES and row.outcome != "discarded") or row.post_id is not None:
                raise PostError("只有尚未发布的待核对视频动态可以放弃")
            if row.outcome == "discarded" and not row.cleanup_pending:
                return response_for_publication(row)
            job_id = _video_job_id(row)
            row.state = "cancelled"
            row.outcome = "discarded"
            row.stage = "complete"
            row.error = "已放弃本次动态，不会自动重新制作或发布"
            row.cleanup_pending = True
            if job_id is not None:
                shared = await db.scalar(
                    select(PostPublication.id)
                    .where(
                        PostPublication.user_id == user_id,
                        PostPublication.id != task_id,
                        PostPublication.outcome.is_distinct_from("discarded"),
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
            row.cleanup_pending = False
            await db.commit()
        return response_for_publication(row)


async def gc_autonomous_publications() -> int:
    """七天后释放完整资料，九十天后清理诊断；请求身份和结果关联保留。"""
    compacted = 0
    cutoff = utc_now() - timedelta(days=7)
    remove_before = utc_now() - timedelta(days=90)
    cursor: int | None = None
    while True:
        async with SESSION_LOCAL() as db:
            statement = (
                select(PostPublication)
                .where(
                    PostPublication.state.in_(_TERMINAL),
                    PostPublication.finished_at < cutoff,
                    PostPublication.cleanup_pending.is_(False),
                    or_(
                        PostPublication.request_json != {},
                        PostPublication.plan_json.is_not(None),
                        PostPublication.checkpoint.is_not(None),
                        PostPublication.error.is_not(None) & (PostPublication.finished_at < remove_before),
                    ),
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
                if is_user_in_maintenance(row.user_id) or (
                    (task := _TASKS.get(row.id)) is not None and not task.done()
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
                changed = bool(row.request_json) or row.plan_json is not None or row.checkpoint is not None
                if changed:
                    row.request_json = {}
                    row.plan_json = None
                    row.checkpoint = None
                    compacted += 1
                if row.error is not None and row.finished_at is not None and row.finished_at < remove_before:
                    row.error = None
            await db.commit()
    return compacted
