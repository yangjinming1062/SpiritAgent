"""所有发布入口共用的独立规划、制作与可恢复任务。"""

import asyncio
from datetime import date
from typing import Any

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    SESSION_LOCAL,
    TaskBag,
    get_logger,
    is_user_in_maintenance,
    parse_llm_json,
    resolve_language,
    resolve_prompt_text,
    track_user_task,
)
from modules.auth import User
from modules.companion import (
    CharacterCardSnapshot,
    CompanionPost,
    Persona,
    PostContentType,
    PostContext,
    PostPlan,
    PostPublication,
    PostPublicationResult,
)
from modules.media import VideoGenJob
from modules.settings import get_user_setting, load_user_settings
from prompts.generation import SELF_VIDEO_KEEP_OUTFIT, SELF_VIDEO_REFERENCE_TEMPLATE
from prompts.posts import POST_PUBLISH_INSTRUCTIONS, POST_REQUEST_CLASSIFICATION
from sqlalchemy import select

from services.application.generation import (
    ImageGenerationError,
    apply_outfit_override,
    build_self_image_prompt,
    enqueue_video_job,
    generate_character_images,
    generate_images,
    load_self_visual_context,
    optional_outfit_image_reference,
    prepare_self_video_reference,
)
from services.domains.companion import (
    character_snapshot_is_current,
    get_scene_state,
    load_companion_prompt_context,
    render_character_identity,
    scene_environment,
)
from services.domains.posts import (
    PostError,
    commit_publication,
    publication_status,
    reserve_publication,
    response_for_publication,
)
from services.infrastructure.assets import save_companion_asset_async
from services.infrastructure.llm import (
    ProviderResultUnknownError,
    call_llm_once,
    resolve_provider_chain,
    resolve_user_llm_config,
    synthesize_speech,
)

logger = get_logger(__name__)
_BG = TaskBag("posts.publication")
_TASKS: dict[str, asyncio.Task[None]] = {}
_TERMINAL = {"published", "partial", "failed", "blocked", "result_unknown", "declined"}
_POSTS_SWITCH = "companion.posts_enabled"


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
        return types


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
) -> dict:
    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        raise PostError("伙伴资料尚未就绪")
    types = await available_types(user_id, autonomous=not requested)
    if not types:
        raise PostError("动态自主发布已关闭")
    if requested_type != "auto" and requested_type not in types:
        raise PostError("请求的动态类型当前不可用")
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
    }


async def compose_plan(user_id: int, payload: dict) -> PostPlan | None:
    async with SESSION_LOCAL() as db:
        config = await resolve_user_llm_config(db, user_id)
    if not config.is_configured:
        raise PostError("模型配置不可用")
    raw = await call_llm_once(
        config,
        resolve_prompt_text(POST_PUBLISH_INSTRUCTIONS, payload["output_language"]),
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
    return plan


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
        if row.post_id is not None:
            return
        if phase is not None:
            row.phase = phase
        if status is not None:
            row.status = status
        row.error = error
        if plan is not None:
            row.plan_json = plan.model_dump(mode="json")
        row.progress_json = {**row.progress_json, **progress}
        await db.commit()


async def _voice(task_id: str, user_id: int, text: str, *, phase: str) -> tuple[str, str]:
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
    path = await save_companion_asset_async(result.audio, user_id=user_id, label="post_voice", ext=ext)
    return path, result.voice or ""


async def _generate_media(row: PostPublication, plan: PostPlan) -> dict:
    progress = dict(row.progress_json)
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
                reference_image=visual.reference_image,
                identity_reference=visual.reference_image,
                secondary_reference_image=outfit,
                identity_text=render_character_identity(identity),
            )
        else:
            urls = await generate_images(plan.prompt, size=plan.size, user_id=row.user_id, persist_user_assets=True)
        if not urls:
            raise PostError("图片生成未取得可用产物")
        progress["media_url"] = urls[0]
    elif plan.content_type == PostContentType.AUDIO:
        progress["media_url"], progress["voice_id"] = await _voice(
            row.id,
            row.user_id,
            plan.text,
            phase="voice_submitting",
        )
    else:
        job_id = progress.get("job_id")
        if job_id is None:
            await _save(row.id, phase="video_submitting", **progress)
            first_frame, prompt = None, plan.prompt
            if visual is not None:
                first_frame = await prepare_self_video_reference(
                    apply_outfit_override(visual, None),
                    row.user_id,
                    prompt=prompt,
                    aspect_ratio=plan.aspect_ratio,
                )
                prompt = (
                    SELF_VIDEO_REFERENCE_TEMPLATE.format(prompt=prompt, outfit=SELF_VIDEO_KEEP_OUTFIT)
                    + "\n"
                    + render_character_identity(identity)
                )
            async with SESSION_LOCAL() as db:
                job = await enqueue_video_job(
                    db,
                    user_id=row.user_id,
                    session_id=None,
                    prompt=prompt,
                    duration=plan.duration,
                    resolution="768P",
                    first_frame_image=first_frame,
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
