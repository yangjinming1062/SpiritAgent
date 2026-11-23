"""独立主动创作、媒体制作与可恢复发布任务。"""

import asyncio
from datetime import timedelta
from typing import Any

from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.prompt_engineer import LlmJsonValidationError, call_llm_json
from infrastructure.ai.providers.http import ProviderResultUnknownError
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.token_budget import json_tokens, material_budget
from infrastructure.ai.user_config import resolve_user_llm_config
from infrastructure.assets.asset_store import dated_asset_directory, save_companion_asset_async
from infrastructure.assets.references import asset_paths
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language, resolve_prompt_text, utc_now
from runtime.background import TaskScope
from runtime.events.emit import emit_ws_event
from runtime.paid_work import GenerationWorkPaused
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.locking import lock_account
from features.appearance.visual_identity import (
    build_plain_image_prompt,
    build_plain_video_prompt,
    build_self_image_inputs,
    build_self_video_inputs,
    load_self_visual_context,
)
from features.assets.lifecycle import cleanup_user_assets, enqueue_asset_cleanup
from features.companion.narrative_prompts import NARRATIVE_REVISION_TEXTS
from features.companion.narrative_quality import NarrativeQualityError, narrative_source_budget, review_narrative
from features.companion.queries import complete_user_ids
from features.configuration.settings.timezone import resolve_user_timezone
from features.configuration.settings.values import load_user_settings
from features.entries.contracts import PostContentType
from features.entries.queries import recent_posts
from features.jobs.service import bind_job_assets
from features.life.context import selected_post_excerpts
from features.media.character_images import (
    ImageChainState,
    generate_reviewed_images,
    run_frozen_image_chain,
)
from features.media.continuations import MediaWait, wait_for_media
from features.media.contracts import VideoJobSnapshot
from features.media.identity_contracts import CharacterReferenceRole
from features.media.image_generation import ImageGenerationError, ImageResultDownloadError
from features.media.queries import get_job, get_video_jobs
from features.media.speech import synthesize_speech
from features.media.video_jobs import (
    accept_checked_video,
    prepare_video_job,
    select_video_resolution,
    submit_video_job,
    video_duration_options,
)
from features.posts.context import fit_post_history, load_post_prompt_context, post_prompt_entry
from features.posts.output_contracts import POST_DECISION, parse_post_decision
from features.posts.prompt_contract import render_post_instructions
from features.posts.prompts import POST_DECISION_REPAIR_TEXTS, POST_MEDIA_NARRATIVE_REVIEW, POST_PUBLISH_INSTRUCTIONS
from features.posts.publication_models import PostPublication
from features.posts.schemas import (
    PostContext,
    PostPlan,
    PostPublicationResult,
)
from features.posts.store import (
    PostBlockedError,
    PostError,
    commit_publication,
    next_planning_publication,
    post_choice_history,
    posts_policy_ok,
    prepare_publication_in_session,
)
from features.scenes.queries import get_scene_state, scene_environment
from features.visuals.queries import prompt_presentation_snapshot

logger = get_logger(__name__)
_TASKS = TaskScope("posts.publication")
_PLANNERS = TaskScope("posts.planning")
_TERMINAL = {"succeeded", "failed", "cancelled"}
# 视频分辨率按此顺序取供应商链能接单的第一档：768P 为默认档，720P 先于 1080P，因为部分供应商模型最高到 720p；动态视频刻意不含 512P/480P 低清档。
_VIDEO_RESOLUTIONS = ("768P", "720P", "1080P")


class _VideoUnsupportedError(PostError):
    """供应商链不支持本次视频的参考模式、时长或分辨率，提交前阻止。"""


class _VideoQualityError(PostError):
    """已取得的原视频未通过自动身份交付准入，不发布。"""


_SERVICE_TYPES = {"image": "image_gen", "video": "video_gen", "audio": "tts"}


class PostMediaWaiting(Exception):
    def __init__(self, dependency_job_id: int) -> None:
        super().__init__("媒体已保留，等待检查或原任务完成")
        self.dependency_job_id = dependency_job_id


async def _post_type_ready(user_id: int, content_type: str, *, needs_provider: bool = True) -> bool:
    async with SESSION_LOCAL() as db:
        if not await posts_policy_ok(db, user_id):
            return False
        if not needs_provider or content_type == "text":
            return True
        return bool(await resolve_provider_chain(db, user_id, _SERVICE_TYPES[content_type], task=AiTaskKey.posts))


async def _post_capabilities(user_id: int) -> tuple[list[str], dict[str, list[int]]]:
    async with SESSION_LOCAL() as db:
        if not await posts_policy_ok(db, user_id):
            raise PostBlockedError("发布开关已关闭")
        types = ["text"]
        for kind in ("image", "audio"):
            if await resolve_provider_chain(db, user_id, _SERVICE_TYPES[kind], task=AiTaskKey.posts):
                types.append(kind)
        try:
            # 动态出镜沿用已确认的身份与现有造型两张参考。
            options = await video_duration_options(
                db,
                user_id,
                task=AiTaskKey.posts,
                preferred=_VIDEO_RESOLUTIONS,
                self_reference_count=2,
            )
        except Exception:
            logger.warning("Post video capabilities unavailable", extra={"user_id": user_id}, exc_info=True)
            options = {"plain": [], "self": []}
        if any(options.values()):
            types.append("video")
        return types, options


async def _creation_input(user_id: int, excerpt_ids: list[int]) -> dict:
    types, video_durations = await _post_capabilities(user_id)
    ctx = await load_post_prompt_context(user_id, publication=True)
    presentation = await prompt_presentation_snapshot(user_id)
    async with SESSION_LOCAL() as db:
        excerpts = await selected_post_excerpts(db, user_id, excerpt_ids)
        config = await resolve_user_llm_config(db, user_id, AiTaskKey.posts)
        base = {
            **ctx.payload,
            "selected_life_excerpts": excerpts,
            "environment": scene_environment(await get_scene_state(db, user_id)),
            "presentation": {
                "visual_description": presentation.visual_description,
                "outfit_description": presentation.outfit_description,
                "scene_description": presentation.scene_description,
            },
            "available_types": types,
            "video_durations": video_durations,
        }
        room = material_budget(config.chain, _creation_instructions(ctx.language), base, POST_DECISION.json_schema())
        choices, more = await post_choice_history(
            db,
            user_id,
            {int(item["source_id"]) for item in excerpts if item["source_id"]},
            token_budget=room,
            exclude=excerpt_ids,
        )
        included = {item["post_id"] for item in choices if "published_post" in item}
        room -= json_tokens(choices)
        recent = []
        cursor = None
        while page := await recent_posts(
            db, user_id, since=ctx.now - timedelta(days=3), until=ctx.now, limit=64, before=cursor
        ):
            for post in page:
                if str(post.id) in included:
                    continue
                card = post_prompt_entry(post, ctx)
                room -= json_tokens(card) + 2
                if room < 0:
                    more = True
                    break
                recent.append(card)
            if room < 0 or len(page) < 64:
                break
            cursor = (page[-1].published_at, page[-1].id)
    return {**base, "previous_post_choices": choices, "recent_posts": recent, "post_history_excerpted": more}


def _creation_instructions(language: str) -> str:
    instructions = render_post_instructions(POST_PUBLISH_INSTRUCTIONS, language)
    instructions += (
        "\nselected_life_excerpts 是你为这次动态选出的片段。可以选一段、合写有关联的几段，或这次不发。保留各段实际发生的时间、范围和事实性质。"
        "excerpt_uses 按摘录填写 excerpt_id 和实际表达它的字段 fields，可选 title、body、prompt（主画面）、text（主音频）或 narration（旁白）。一次摘录可用于多个部分，独立创作填空数组。previous_post_choices 记录同一经历的早先选择，同一成稿由 post_id 关联。post_history_excerpted 为 true 表示仅提供部分历史。"
        "仅有新的感受、进展或表达角度时再谈。used_in_post 为 null 表示无法确认当时是否采用。"
        "近期用户发言只用于相处背景，私人交流、关系理解及记忆不直接成为动态素材。"
        if language == "zh"
        else "\nThe selected_life_excerpts are passages you chose for this post. Use one, connect related passages, or decline, "
        "preserving the date, scope and factual status of each. For each excerpt used, include its excerpt_id and the fields that express it in excerpt_uses: title, body, prompt (main visual), text (main audio), or narration. Use [] for independent work. "
        "previous_post_choices records prior choices linked to published content by post_id. When post_history_excerpted is true, the history is partial. Revisit a source for a new insight, development or purpose. A null used_in_post means unknown. Recent user messages inform the relationship. Keep private exchanges, "
        "relationship interpretations and memories out of the post itself."
    )
    return instructions


async def compose_plan(user_id: int, payload: dict) -> PostPlan | None:
    async with SESSION_LOCAL() as db:
        config = await resolve_user_llm_config(db, user_id, AiTaskKey.posts)
    if not config.is_configured:
        raise PostError("模型配置不可用")
    language = payload["output_language"]
    instructions = _creation_instructions(language)
    choices, recent = payload["previous_post_choices"], payload["recent_posts"]
    base = {**payload, "previous_post_choices": [], "recent_posts": []}
    excerpted = base["post_history_excerpted"]
    creation_payload = base

    def parse_output(value: Any) -> PostPlan | None:
        plan = parse_post_decision(value)
        if plan is not None:
            uses = plan.excerpt_uses
            ids = [str(use.excerpt_id) for use in uses]
            available = {item["id"] for item in payload["selected_life_excerpts"]}
            if (
                len(set(ids)) != len(ids)
                or not set(ids) <= available
                or any(not getattr(plan, field) for use in uses for field in use.fields)
            ):
                raise LlmJsonValidationError("invalid_excerpt_uses", fields=("excerpt_uses",))
        if plan is not None and plan.content_type.value not in payload["available_types"]:
            raise LlmJsonValidationError("unavailable_post_type", fields=("content_type",))
        if plan is not None and plan.content_type == PostContentType.VIDEO:
            mode = "self" if plan.depicts_self else "plain"
            if plan.duration not in payload["video_durations"][mode]:
                raise LlmJsonValidationError("unsupported_video_duration", fields=("duration", "depicts_self"))
        return plan

    schema = POST_DECISION.json_schema()
    for revision in range(2):
        room = material_budget(config.chain, instructions, creation_payload, schema)
        history = fit_post_history(choices, recent, room, excerpted=excerpted)
        plan = await call_llm_json(
            config,
            instructions,
            {**creation_payload, **history},
            schema=schema,
            schema_name="post_decision",
            parse_output=parse_output,
            repair_prompt=resolve_prompt_text(POST_DECISION_REPAIR_TEXTS, language),
        )
        if plan is None:
            return None
        plan = _drop_inapplicable_fields(user_id, plan, payload["available_types"])
        artwork = plan.content_type in {PostContentType.IMAGE, PostContentType.VIDEO}
        source = (
            {**base, "planned_artwork": {"medium": plan.content_type.value, "brief": plan.prompt}} if artwork else base
        )
        draft = {
            field: value
            for field in ("publication_intent", "title", "body", "text", "narration")
            if (value := getattr(plan, field))
        }
        requirements = resolve_prompt_text(POST_MEDIA_NARRATIVE_REVIEW, language) if artwork else ""
        room = narrative_source_budget(config, source, draft, language, requirements=requirements)
        review_history = fit_post_history(
            history["previous_post_choices"], history["recent_posts"], room, excerpted=history["post_history_excerpted"]
        )
        review = await review_narrative(
            config, {**source, **review_history}, draft, language, requirements=requirements
        )
        if not review.issues:
            return plan
        if revision:
            fields = {issue.field for issue in review.issues}
            if artwork and fields <= {"body", "narration"}:
                return plan.model_copy(update=dict.fromkeys(fields, ""))
            raise NarrativeQualityError(review)
        creation_payload = {
            **base,
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
        result = await prepare_publication_in_session(db, user_id)
        if result is None:
            raise PostBlockedError("动态自主发布已关闭或账户不可用")
        await db.commit()
    wake_post_planning(user_id)
    return result


def wake_post_planning(user_id: int) -> None:
    _PLANNERS.schedule(user_id, lambda: _plan_publications(user_id), name=f"post.plan.{user_id}", user_id=user_id)


def schedule_publication(task_id: int, user_id: int) -> None:
    _TASKS.spawn(task_id, lambda: _run_publication(task_id, user_id), name=f"post.publish.{task_id}", user_id=user_id)


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
        if "image_chain" in progress:
            await bind_job_assets(
                db,
                row.user_id,
                row.id,
                asset_paths(progress["image_chain"], row.user_id) - {progress["image_chain"].get("pending_path")},
                "generation",
            )
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
        result.audio, user_id=user_id, label="post_voice", ext=ext, directory=directory
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
    if plan.depicts_self and row.video_job_id is None and not progress.get("image_chain"):
        presentation = await prompt_presentation_snapshot(row.user_id)
        visual_plan = await load_self_visual_context(row.user_id, presentation)
    if visual_plan is not None:
        identity = visual_plan.context.identity
        progress["identity"] = identity.model_dump(mode="json")
    if plan.content_type == PostContentType.IMAGE:
        chain = ImageChainState.model_validate(progress.get("image_chain", {}))

        async def save_image(value: ImageChainState) -> None:
            progress["image_chain"] = value.model_dump(mode="json")
            await _save(row.id, phase="image", **progress)

        inputs = build_self_image_inputs(visual_plan, plan.prompt) if visual_plan else None
        await _save(row.id, phase="image_submitting" if chain.inputs is None else "image", **progress)
        urls = (
            await run_frozen_image_chain(row.user_id, chain, task=AiTaskKey.posts, save_progress=save_image)
            if chain.inputs is not None
            else await generate_reviewed_images(
                inputs.prompt if inputs else build_plain_image_prompt(plan.prompt),
                size=plan.size,
                user_id=row.user_id,
                storage_directory=directory,
                reference_image=inputs.reference_image if inputs else "",
                identity_reference=inputs.reference_image if inputs else "",
                secondary_reference_image=inputs.secondary_reference_image if inputs else None,
                identity_reference_mode=inputs.identity_reference_mode if inputs else "primary",
                identity_required=inputs is not None,
                goal_review="required",
                delivery_goal=plan.prompt,
                pose_goal=inputs.pose_goal if inputs else None,
                task=AiTaskKey.posts,
                state=chain,
                save_progress=save_image,
            )
        )
        selected = chain.delivery_candidate(0, chain.delivery_dimensions())
        if not urls or selected is None or selected.review is None:
            if chain.unknown_submission():
                raise ImageGenerationError("制作结果尚未核实", result_unknown=True)
            raise ImageGenerationError("图片尚未取得可用结果")
        progress["media_url"], progress["media_review"] = selected.path, selected.review.model_dump(mode="json")
        progress["media_selection"] = chain.selections[0].model_dump(mode="json")
    elif plan.content_type == PostContentType.AUDIO:
        progress["media_url"], progress["voice_id"] = await _voice(
            row.id, row.user_id, plan.text, phase="voice_submitting", directory=directory
        )
    else:
        job_id = row.video_job_id
        if job_id is None:
            references: tuple[str, ...] = ()
            reference_guidance = ""
            identity_reference_role: CharacterReferenceRole = "portrait"
            if visual_plan is not None:
                inputs = await build_self_video_inputs(visual_plan)
                prompt, references = plan.prompt, inputs.references
                reference_guidance = inputs.reference_guidance
                identity_reference_role = inputs.identity_reference_role
            else:
                prompt = build_plain_video_prompt(plan.prompt)
            resolution = await select_video_resolution(
                row.user_id,
                reference_images=bool(references),
                reference_image_count=len(references),
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
                    goal=plan.prompt,
                    duration=plan.duration,
                    resolution=resolution,
                    include_upscaled=False,
                    reference_images=references,
                    reference_guidance=reference_guidance,
                    performance_context=visual_plan.context.performance_context if visual_plan else "",
                    portrait_reference=visual_plan.context.portrait_path if visual_plan else None,
                    aspect_ratio=plan.aspect_ratio,
                    identity_reference_path=visual_plan.context.fullbody_path if visual_plan else None,
                    identity=identity,
                    identity_reference_role=identity_reference_role,
                    task=AiTaskKey.posts,
                )
                publication.video_job_id = job.id
                publication.stage = "video_waiting"
                await wait_for_media(
                    db,
                    user_id=row.user_id,
                    owner_kind="post.publish",
                    owner_job_id=row.id,
                    dependency_job_id=job.id,
                    operation_id=f"post:{row.source_key}:publish",
                    automatic=True,
                    context={"plan": publication.plan_json},
                )
                await db.commit()
            job_id = job.id
            await submit_video_job(row.user_id, job_id)
        async with SESSION_LOCAL() as db:
            job = await get_job(db, job_id, row.user_id)
        if job is None:
            raise PostError("原视频任务不可用")
        if job.status == "failed":
            raise _VideoQualityError(job.error_message or "视频未通过检查")
        if job.status != "succeeded":
            raise PostMediaWaiting(job_id)
        if job.receive_only:
            progress["receive_only"] = True
        progress["media_url"] = job.video_url
        progress["media_review"] = job.review.model_dump(mode="json") if job.review else None
        progress["media_selection"] = job.selection.model_dump(mode="json") if job.selection else None
    await _save(row.id, phase="media_ready", **progress)
    return progress


async def _plan_publications(user_id: int) -> None:
    """同账户按受理顺序构思；冻结后到达的摘录由下一个任务接续。"""
    while not is_user_in_maintenance(user_id):
        async with SESSION_LOCAL() as db:
            await lock_account(db, user_id)
            row = await next_planning_publication(db, user_id)
            if row is None:
                return
            task_id = row.id
            payload = row.request_json
            excerpt_ids = (row.checkpoint or {}).get("excerpt_ids", [])
            enabled = await posts_policy_ok(db, user_id)
            row.state = "running"
            await db.commit()
        try:
            if not enabled:
                raise PostBlockedError("动态自主发布已关闭或账户不可用")
            if not payload:
                payload = await _creation_input(user_id, excerpt_ids)
                await _save(task_id, request=payload)
            plan = await compose_plan(user_id, payload)
            if plan is None:
                await _save(task_id, status="declined", phase="complete")
            else:
                await _save(task_id, plan=plan, phase="planned")
                schedule_publication(task_id, user_id)
        except PostBlockedError as exc:
            await _save(task_id, status="blocked", error=str(exc))
        except NarrativeQualityError as exc:
            await _save(task_id, status="failed", error=str(exc))
        except Exception:
            logger.exception("Post planning failed", extra={"publication_id": task_id})
            await _save(task_id, status="failed", error="动态构思失败，内容未发布")


async def _run_publication(task_id: int, user_id: int) -> None:
    try:
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(PostPublication).where(PostPublication.id == task_id, PostPublication.user_id == user_id)
            )
        if row is None or row.state not in {"queued", "running"} or row.stage == "planning":
            return
        if row.stage == "narration_submitting" and (row.checkpoint or {}).get("media_url"):
            row.checkpoint = {**(row.checkpoint or {}), "narration_failed": True}
            await _save(task_id, phase="media_ready", **(row.checkpoint or {}))
        elif row.stage.endswith("_submitting"):
            await _save(task_id, status="result_unknown", error="制作请求的结果尚未确认，未重复提交")
            return
        if is_user_in_maintenance(row.user_id):
            return
        plan = PostPlan.model_validate(row.plan_json)
        has_video_job = plan.content_type == PostContentType.VIDEO and row.video_job_id is not None
        if not await _post_type_ready(row.user_id, plan.content_type.value, needs_provider=not has_video_job):
            await _save(task_id, status="blocked", error="发布开关或供应商能力不可用")
            return
        await _save(task_id, status="running")
        progress = await _generate_media(row, plan)
        if (
            plan.narration
            and plan.content_type in (PostContentType.IMAGE, PostContentType.VIDEO)
            and not progress.get("audio_url")
            and not progress.get("narration_failed")
            and not progress.get("receive_only")
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
        async with SESSION_LOCAL() as db:
            await commit_publication(
                db,
                task_id,
                user_id,
                title=plan.title,
                body=plan.body,
                content_type=plan.content_type,
                media_url=progress.get("media_url"),
                audio_url=progress.get("audio_url"),
                context=PostContext(
                    used_excerpt_ids=plan.published_excerpt_ids(has_narration=bool(progress.get("audio_url"))),
                    publication_intent=plan.publication_intent,
                    creation_intent=plan.prompt,
                    transcript=plan.text,
                    narration=plan.narration if progress.get("audio_url") else "",
                    voice_id=progress.get("voice_id", ""),
                ),
                partial=bool(plan.narration and not progress.get("audio_url")),
            )
    except asyncio.CancelledError:
        raise
    except GenerationWorkPaused:
        return
    except _VideoUnsupportedError:
        await _save(task_id, status="blocked", error="视频供应商不支持本次视频要求")
    except _VideoQualityError as exc:
        await _save(task_id, status="failed", error=str(exc))
    except PostBlockedError as exc:
        await _save(task_id, status="blocked", error=str(exc))
    except ProviderResultUnknownError:
        await _save(task_id, status="result_unknown", error="制作请求结果未知，未重复提交")
    except PostMediaWaiting as exc:
        await _wait_publication(task_id, exc.dependency_job_id)
    except ImageResultDownloadError:
        async with SESSION_LOCAL() as db:
            saved = await db.get(PostPublication, task_id)
        chain = ImageChainState.model_validate(saved.checkpoint["image_chain"])
        chain.resume_download()
        await _save(task_id, status="queued", phase="image_receiving", image_chain=chain.model_dump(mode="json"))
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
        rows = await db.execute(
            select(PostPublication.id, PostPublication.user_id, PostPublication.stage)
            .where(PostPublication.state.in_(("queued", "running")))
            .order_by(PostPublication.id)
        )
    planning_users: set[int] = set()
    for task_id, user_id, stage in rows:
        if stage == "planning":
            planning_users.add(user_id)
        else:
            schedule_publication(task_id, user_id)
    for user_id in planning_users:
        wake_post_planning(user_id)


async def drain_publications() -> None:
    await _PLANNERS.drain()
    await _TASKS.drain()


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
                if is_user_in_maintenance(row.user_id) or _TASKS.running(row.id) is not None:
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
                job_id = row.video_job_id
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


async def _wait_publication(task_id: int, dependency_job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(PostPublication, task_id, with_for_update=True)
        if row is None or row.post_id is not None or row.state in _TERMINAL:
            return
        row.state, row.stage, row.error = "paused", "video_waiting", None
        await wait_for_media(
            db,
            user_id=row.user_id,
            owner_kind="post.publish",
            owner_job_id=row.id,
            dependency_job_id=dependency_job_id,
            operation_id=f"post:{row.source_key}:publish",
            automatic=True,
            context={"plan": row.plan_json},
        )
        await db.commit()


async def resume_post_media(db: AsyncSession, wait: MediaWait) -> dict | None:
    row = await db.scalar(
        select(PostPublication)
        .where(PostPublication.id == wait.owner_job_id, PostPublication.user_id == wait.user_id)
        .with_for_update()
    )
    if row is None or row.state in {"cancelled", "failed"}:
        return {"finished": True}
    if row.post_id is not None:
        return {"post_id": str(row.post_id)}
    if row.state in {"queued", "running"}:
        return None
    job = await get_job(db, wait.dependency_job_id, wait.user_id)
    if job and job.status not in {"succeeded", "failed", "discarded"}:
        if job.status == "result_unknown":
            row.state, row.stage = "unknown", "video_waiting"
        return None
    if (
        not job
        or job.status != "succeeded"
        or row.video_job_id != wait.dependency_job_id
        or row.plan_json != wait.context_json["plan"]
        or not await accept_checked_video(db, wait.user_id, job.id)
    ):
        row.state, row.stage, row.error = "failed", "complete", "本次动态没有可发布的媒体"
        return {"finished": True}
    if row.state == "unknown":
        row.checkpoint = {**(row.checkpoint or {}), "receive_only": True}
    row.state, row.stage = "queued", "media_ready"
    emit_ws_event(
        db, user_id=wait.user_id, event_type="post.publication.continue", payload={"publication_id": str(row.id)}
    )
    return None


async def publication_continue_requested(user_id: int, payload: dict) -> None:
    schedule_publication(int(payload["publication_id"]), user_id)
