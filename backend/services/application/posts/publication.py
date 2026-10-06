"""所有发布入口共用的独立规划、制作与可恢复任务。"""

import asyncio
from datetime import date, timedelta
from typing import Any, Literal, get_args
from uuid import UUID
from weakref import WeakValueDictionary

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    SESSION_LOCAL,
    TaskBag,
    ensure_utc,
    get_logger,
    is_user_in_maintenance,
    parse_llm_json,
    resolve_language,
    resolve_prompt_text,
    track_user_task,
    utc_now,
)
from modules.auth import User, lock_user_row
from modules.companion import (
    CharacterCardSnapshot,
    CompanionPost,
    Persona,
    PostContentType,
    PostContext,
    PostPlan,
    PostPublication,
    PostPublicationRecovery,
    PostPublicationRecoveryList,
    PostPublicationResult,
)
from modules.media import VideoGenJob
from modules.scheduler import NightlyActivityAction
from modules.settings import get_user_setting, load_user_settings, resolve_user_timezone
from prompts.posts import POST_PUBLISH_INSTRUCTIONS, POST_REQUEST_CLASSIFICATION
from sqlalchemy import or_, select, update

from services.application.generation import (
    ImageGenerationError,
    apply_outfit_override,
    build_self_image_prompt,
    build_self_video_prompt,
    discard_post_video_job,
    enqueue_video_job,
    generate_character_images,
    generate_images,
    load_self_visual_context,
    optional_outfit_image_reference,
    post_video_asset,
    post_video_ready,
    query_post_video_job,
    select_video_resolution,
    self_video_references,
    video_generation_wait_seconds,
)
from services.domains.assets import cleanup_user_assets, enqueue_asset_cleanup
from services.domains.companion import (
    character_snapshot_is_current,
    get_scene_state,
    load_companion_prompt_context,
    render_character_identity,
    scene_environment,
)
from services.domains.posts import (
    PostBlockedError,
    PostError,
    PostNotFoundError,
    commit_publication,
    publication_quota_remaining,
    publication_status,
    reserve_publication,
    response_for_publication,
)
from services.infrastructure.assets import (
    dated_asset_directory,
    parse_companion_asset_path,
    resolve_companion_asset_path,
    save_companion_asset_async,
    signed_companion_asset_url,
)
from services.infrastructure.llm import (
    ProviderResultUnknownError,
    call_llm_once,
    resolve_provider_chain,
    resolve_user_llm_config,
    synthesize_speech,
)

from .prompt_contract import render_post_instructions

logger = get_logger(__name__)
_BG = TaskBag("posts.publication")
_TASKS: dict[str, asyncio.Task[None]] = {}
_TERMINAL = {"published", "partial", "failed", "blocked", "result_unknown", "declined", "discarded"}
_RECOVERY_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_POSTS_SWITCH = "companion.posts_enabled"
# 视频分辨率按此顺序取供应商链能接单的第一档：768P 为默认档，720P 先于 1080P，因为部分供应商模型最高到 720p；动态视频刻意不含 512P/480P 低清档。
_VIDEO_RESOLUTIONS = ("768P", "720P", "1080P")
# 动态计划允许的视频时长。
_VIDEO_DURATIONS: tuple[int, ...] = get_args(PostPlan.model_fields["duration"].annotation)


class _VideoUnsupportedError(PostError):
    """供应商链不支持本次视频的参考模式、时长或分辨率，提交前阻止。"""


async def available_types(user_id: int, *, autonomous: bool) -> list[str]:
    async with SESSION_LOCAL() as db:
        if not await db.scalar(select(User.is_active).where(User.id == user_id)):
            return []
        enabled = await get_user_setting(db, user_id, _POSTS_SWITCH)
        if autonomous and enabled is not None and enabled is not True:
            return []
        types = ["text"]
        for content_type, service_type in (("image", "image_gen"), ("video", "video_gen"), ("audio", "tts")):
            if await resolve_provider_chain(
                db,
                user_id,
                service_type,
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
            ):
                return True
    except Exception:
        logger.warning("Post video capability check failed", extra={"user_id": user_id}, exc_info=True)
    return False


async def _requested_by_user(user_id: int, user_message: str, intent: str) -> bool:
    if not user_message:
        return False
    async with SESSION_LOCAL() as db:
        config = await resolve_user_llm_config(db, user_id)
        language = await get_user_setting(db, user_id, "language")
    if not config.is_configured:
        raise PostError("模型配置不可用")
    raw = await call_llm_once(
        config,
        resolve_prompt_text(POST_REQUEST_CLASSIFICATION, language),
        {"user_message": user_message, "intent": intent},
        json_output=True,
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
    )
    data = parse_llm_json(raw)
    if not isinstance(data, dict) or set(data) != {"user_requested"} or type(data["user_requested"]) is not bool:
        raise PostError("发布请求判断格式无效")
    return data["user_requested"]


async def _freeze_input(
    user_id: int,
    *,
    intent: str,
    requested_type: str,
    user_message: str,
    requested: bool,
    companion_activity_facts: tuple[str, ...],
) -> dict:
    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        raise PostError("伙伴资料尚未就绪")
    types = await available_types(user_id, autonomous=not requested)
    if not types:
        raise PostBlockedError("动态自主发布已关闭")
    if requested_type != "auto" and requested_type not in types:
        raise PostBlockedError("请求的动态类型当前不可用")
    async with SESSION_LOCAL() as db:
        recent = (
            await db.execute(
                select(CompanionPost.title, CompanionPost.body)
                .where(CompanionPost.user_id == user_id)
                .order_by(CompanionPost.published_at.desc())
                .limit(10),
            )
        ).all()
        environment = scene_environment(await get_scene_state(db, user_id))
    return {
        "intent": intent,
        "requested_type": requested_type,
        "user_message": user_message,
        "user_requested": requested,
        "output_language": ctx.language,
        "current_time": ctx.current_time,
        "persona": ctx.persona_extras,
        "long_term_memories": ctx.memories_block,
        "current_mood": ctx.current_mood,
        "environment": environment,
        "available_types": types,
        "recent_posts": [{"title": t, "body": b} for t, b in recent],
        **({"companion_activity_facts": list(companion_activity_facts)} if companion_activity_facts else {}),
    }


async def compose_plan(user_id: int, payload: dict) -> PostPlan | None:
    async with SESSION_LOCAL() as db:
        config = await resolve_user_llm_config(db, user_id)
    if not config.is_configured:
        raise PostError("模型配置不可用")
    raw = await call_llm_once(
        config,
        render_post_instructions(POST_PUBLISH_INSTRUCTIONS, payload["output_language"]),
        payload,
        json_output=True,
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
    )
    data = parse_llm_json(raw)
    if not isinstance(data, dict) or type(data.get("post")) is not bool:
        raise PostError("发布决策格式无效")
    if not data["post"]:
        if set(data) != {"post"}:
            raise PostError("发布决策含无关字段")
        return None
    if set(data) != {"post", "plan"}:
        raise PostError("发布决策字段无效")
    plan = PostPlan.model_validate(data["plan"])
    if plan.content_type.value not in payload["available_types"] or (
        payload["requested_type"] != "auto" and plan.content_type.value != payload["requested_type"]
    ):
        raise PostError("发布决策选择了不可用的类型")
    return _drop_inapplicable_fields(user_id, plan, payload["available_types"])


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


async def request_publication(
    user_id: int,
    *,
    key: str,
    trigger: str,
    intent: str,
    requested_type: str = "auto",
    user_message: str = "",
    autonomous: bool = True,
    activity_date: date | None = None,
    companion_activity_facts: tuple[str, ...] = (),
) -> PostPublicationResult:
    async with SESSION_LOCAL() as db:
        existing = await db.scalar(
            select(PostPublication).where(
                PostPublication.user_id == user_id,
                PostPublication.idempotency_key == key,
            ),
        )
        if existing is not None:
            schedule_publication(existing.id, user_id)
            return response_for_publication(existing)
    if is_user_in_maintenance(user_id):
        raise PostError("账户正在维护，暂不能发布")
    if not intent.strip() or len(intent) > 1000 or requested_type not in ("auto", "text", "image", "video", "audio"):
        raise PostError("发布意图或类型无效")
    requested = not autonomous and await _requested_by_user(user_id, user_message, intent)
    payload = await _freeze_input(
        user_id,
        intent=intent,
        requested_type=requested_type,
        user_message=user_message,
        requested=requested,
        companion_activity_facts=companion_activity_facts,
    )
    async with SESSION_LOCAL() as db:
        row = await reserve_publication(
            db,
            user_id,
            key=key,
            trigger=trigger,
            quota_kind="user_requested" if requested else "autonomous",
            activity_date=activity_date or date.fromisoformat(payload["current_time"][:10]),
            request=payload,
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


def _owned_assets(progress: dict[str, Any]) -> list[str]:
    """任务自己保存的图片、语音与旁白；视频动态的主媒体是视频任务的成品，由该任务管理。"""
    paths = [progress.get("audio_url")]
    if progress.get("job_id") is None:
        paths.append(progress.get("media_url"))
    return [path for path in paths if path]


async def _save(
    task_id: str,
    *,
    phase: str | None = None,
    status: str | None = None,
    error: str | None = None,
    plan: PostPlan | None = None,
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
        row.progress_json = {**row.progress_json, **progress}
        # 阻止或失败的任务不会产生动态（已有动态时上方已返回），其保存的资产随之删除；结果未知的任务保留已有产物。
        discarded = _owned_assets(row.progress_json) if status in ("blocked", "failed") else []
        await enqueue_asset_cleanup(db, row.user_id, discarded)
        user_id = row.user_id
        await db.commit()
    if discarded:
        await cleanup_user_assets(user_id)


async def _voice(task_id: str, user_id: int, text: str, *, phase: str, directory: str) -> tuple[str, str]:
    async with SESSION_LOCAL() as db:
        if not await db.scalar(select(Persona.is_complete).where(Persona.user_id == user_id)):
            raise PostError("伙伴资料尚未就绪")
        settings = await load_user_settings(db, user_id, ("companion.voice_id", "language"))
    await _save(task_id, phase=phase)
    result = await synthesize_speech(
        user_id,
        text,
        settings.get("companion.voice_id") or "",
        resolve_language(settings.get("language")),
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
    if plan.content_type == PostContentType.TEXT or progress.get("media_url"):
        return progress
    identity = None
    visual = await load_self_visual_context(row.user_id) if plan.depicts_self and not progress.get("job_id") else None
    if visual is not None:
        identity = visual.identity
        progress["identity"] = identity.model_dump(mode="json")
    if plan.content_type == PostContentType.IMAGE:
        await _save(row.id, phase="image_submitting", **progress)
        if visual is not None:
            visual_plan = apply_outfit_override(visual, None)
            outfit = await optional_outfit_image_reference(visual_plan, row.user_id)
            urls = await generate_character_images(
                build_self_image_prompt(visual_plan, plan.prompt, has_outfit_reference=bool(outfit)),
                size=plan.size,
                user_id=row.user_id,
                storage_directory=directory,
                reference_image=visual.reference_image,
                identity_reference=visual.reference_image,
                secondary_reference_image=outfit,
                identity_text=render_character_identity(identity),
            )
        else:
            urls = await generate_images(
                plan.prompt,
                size=plan.size,
                user_id=row.user_id,
                persist_user_assets=True,
                storage_directory=directory,
            )
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
        job_id = progress.get("job_id")
        if job_id is None:
            references, prompt = (), plan.prompt
            if visual is not None:
                visual_plan = apply_outfit_override(visual, None)
                references = self_video_references(visual_plan)
                prompt = build_self_video_prompt(visual_plan, prompt, has_outfit_reference=len(references) > 1)
            resolution = await select_video_resolution(
                row.user_id,
                reference_images=bool(references),
                duration=plan.duration,
                preferred=_VIDEO_RESOLUTIONS,
            )
            if resolution is None:
                raise _VideoUnsupportedError
            await _save(row.id, phase="video_submitting", **progress)
            async with SESSION_LOCAL() as db:
                job = await enqueue_video_job(
                    db,
                    user_id=row.user_id,
                    session_id=None,
                    asset_directory=directory,
                    prompt=prompt,
                    duration=plan.duration,
                    resolution=resolution,
                    reference_images=references,
                    aspect_ratio=plan.aspect_ratio,
                    identity_reference_path=visual.reference_path if visual else None,
                    identity=identity,
                )
            job_id = job.id
            progress["job_id"] = job_id
            await _save(row.id, phase="video_waiting", **progress)
        while True:
            async with SESSION_LOCAL() as db:
                job = await db.get(VideoGenJob, job_id)
            if job is None or job.user_id != row.user_id or job.status == "failed":
                raise PostError("视频生成失败")
            if job.status == "result_unknown":
                raise ProviderResultUnknownError("POST", "")
            if job.status == "succeeded":
                progress["media_url"] = job.video_url
                break
            # 任务可能停在下载失败、等待重启恢复的非终态；按任务创建时间计时，重启后仍受同一预算约束，超时按结果未知收尾。
            if utc_now() >= ensure_utc(job.created_at) + timedelta(seconds=video_generation_wait_seconds(job)):
                logger.warning("Post video wait budget exhausted", extra={"publication_id": row.id, "job_id": job_id})
                raise ProviderResultUnknownError("POST", "")
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
            if not await available_types(row.user_id, autonomous=row.quota_kind == "autonomous"):
                await _save(task_id, status="blocked", error="发布开关已关闭")
                return
            await _save(task_id, status="running", phase="planning")
            plan = await compose_plan(row.user_id, row.request_json)
            if plan is None:
                await _save(task_id, status="declined", phase="complete")
                return
            await _save(task_id, plan=plan, phase="planned")
        else:
            plan = PostPlan.model_validate(row.plan_json)
        if plan.content_type.value not in await available_types(row.user_id, autonomous=row.quota_kind == "autonomous"):
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
                voice_types = await available_types(row.user_id, autonomous=row.quota_kind == "autonomous")
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
        if plan.content_type.value not in await available_types(row.user_id, autonomous=row.quota_kind == "autonomous"):
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
                    publication_intent=row.request_json.get("intent", ""),
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


async def await_publication(user_id: int, task_id: str) -> PostPublicationResult:
    task = _TASKS.get(task_id)
    if task is not None:
        await asyncio.shield(task)
    async with SESSION_LOCAL() as db:
        return await publication_status(db, user_id, task_id)


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
    value = row.progress_json.get("job_id")
    return value if type(value) is int and value > 0 else None


def _recovery_response(row: PostPublication, job: VideoGenJob | None) -> PostPublicationRecovery:
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
            can_adopt = row.status == "result_unknown" and post_video_ready(job)
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
            row.status == "result_unknown"
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
        if query and row.status == "result_unknown" and job_id is not None:
            try:
                job = await query_post_video_job(user_id, job_id)
            except Exception as exc:
                logger.warning("original post video query failed", extra={"publication_id": task_id}, exc_info=True)
                raise PostError("原视频任务暂时无法查询，请稍后再试") from exc
        else:
            async with SESSION_LOCAL() as db:
                job = (
                    await db.scalar(select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id))
                    if job_id
                    else None
                )
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
                            PostPublication.status == "result_unknown",
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
        jobs = {
            job.id: job
            for job in (
                await db.scalars(select(VideoGenJob).where(VideoGenJob.id.in_(job_ids), VideoGenJob.user_id == user_id))
            ).all()
        }
    return PostPublicationRecoveryList(
        items=[_recovery_response(row, jobs.get(_video_job_id(row))) for row in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )


async def adopt_publication_video(user_id: int, task_id: str) -> PostPublicationResult:
    """明确采纳现有视频：原任务幂等，按采纳时政策、身份和额度发布，不重新制作旁白。"""
    if is_user_in_maintenance(user_id):
        raise PostError("账户正在维护，暂不能采纳")
    async with _recovery_lock(task_id), SESSION_LOCAL() as db:
        await lock_user_row(db, user_id)
        row = await db.scalar(
            select(PostPublication)
            .where(PostPublication.id == task_id, PostPublication.user_id == user_id)
            .with_for_update(),
        )
        if row is None:
            raise PostNotFoundError("找不到发布任务")
        if row.post_id is not None:
            return response_for_publication(row)
        if row.status != "result_unknown" or row.plan_json is None:
            raise PostError("只有待核对的视频动态可以采纳")
        user = await db.get(User, user_id, populate_existing=True)
        if (
            user is None
            or not user.is_active
            or not await db.scalar(select(Persona.is_complete).where(Persona.user_id == user_id))
        ):
            raise PostBlockedError("账户或伙伴资料不可用")
        enabled = await get_user_setting(db, user_id, _POSTS_SWITCH)
        if row.quota_kind == "autonomous" and enabled is not None and enabled is not True:
            raise PostBlockedError("动态自主发布已关闭")
        remaining = await publication_quota_remaining(db, user_id, row.quota_kind, exclude_publication_id=row.id)
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
                publication_intent=row.request_json.get("intent", ""),
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
            if row.status not in ("result_unknown", "discarded") or row.post_id is not None:
                raise PostError("只有尚未发布的待核对视频动态可以放弃")
            if row.status == "discarded" and not row.progress_json.get("discard_cleanup_pending"):
                return response_for_publication(row)
            job_id = _video_job_id(row)
            row.status = "discarded"
            row.phase = "complete"
            row.error = "已放弃本次动态，不会自动重新制作或发布"
            row.progress_json = {**row.progress_json, "discard_cleanup_pending": True}
            await enqueue_asset_cleanup(db, user_id, _owned_assets(row.progress_json))
            await db.commit()
        if job_id is not None:
            try:
                await discard_post_video_job(user_id, job_id, task_id)
            except ValueError as exc:
                raise PostError(str(exc)) from exc
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
    """随机自主任务完整资料保留七天、精简记录九十天；未知、在途和引用职责不删除。"""
    compacted = deleted = 0
    cutoff = utc_now() - timedelta(days=7)
    remove_before = utc_now() - timedelta(days=90)
    cursor: str | None = None
    while True:
        async with SESSION_LOCAL() as db:
            statement = (
                select(PostPublication)
                .where(
                    PostPublication.trigger == "autonomous",
                    PostPublication.quota_kind == "autonomous",
                    PostPublication.status.in_(_TERMINAL - {"result_unknown"}),
                    PostPublication.updated_at < cutoff,
                    PostPublication.idempotency_key.startswith("autonomous:"),
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
            for row in rows:
                try:
                    key = UUID(row.idempotency_key.removeprefix("autonomous:"))
                except ValueError:
                    continue
                if (
                    key.version != 4
                    or row.progress_json.get("discard_cleanup_pending")
                    or is_user_in_maintenance(row.user_id)
                    or ((task := _TASKS.get(row.id)) is not None and not task.done())
                ):
                    continue
                reference = await db.scalar(
                    select(NightlyActivityAction.id)
                    .where(NightlyActivityAction.result["publication_id"].as_string() == row.id)
                    .limit(1),
                )
                if reference is not None:
                    continue
                job_id = _video_job_id(row)
                if job_id is not None:
                    job = await db.get(VideoGenJob, job_id)
                    if job is not None and job.status not in ("succeeded", "failed", "discarded"):
                        continue
                keep = {
                    key: value
                    for key, value in row.progress_json.items()
                    if key in ("job_id", "media_url", "audio_url", "voice_id", "narration_failed")
                }
                if row.updated_at < remove_before and row.post_id is None and not keep:
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
