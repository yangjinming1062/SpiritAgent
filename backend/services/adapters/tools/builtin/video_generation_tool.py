import asyncio
import json
from datetime import timedelta

from components import SESSION_LOCAL, SETTINGS, get_logger, tool_error, utc_now
from prompts.generation import (
    IMAGE_ANIMATION_TEMPLATE,
    SELF_VIDEO_KEEP_OUTFIT,
    SELF_VIDEO_REFERENCE_TEMPLATE,
)
from prompts.tools import (
    VIDEO_GENERATION_DESC,
    VIDEO_GENERATION_PARAM_DESCS,
    VIDEO_STATUS_DESC,
    VIDEO_STATUS_PARAM_DESCS,
)

from services.application.generation import (
    AvatarGenerationError,
    ImageGenerationError,
    apply_outfit_override,
    enqueue_video_job,
    get_job,
    load_self_visual_context,
    prepare_self_video_reference,
)
from services.domains.companion import render_character_identity
from services.infrastructure.llm import MissingLlmConfigError, VisualReasoningError
from services.infrastructure.tool_runtime import REGISTRY

logger = get_logger(__name__)


async def video_generation_tool(
    prompt: str,
    duration: int = 6,
    resolution: str = "768P",
    first_frame_image: str | None = None,
    aspect_ratio: str | None = None,
    user_id: int | None = None,
    parent_session_id: str | None = None,
    subject: str | None = None,
    outfit_override: str | None = None,
    **_,
) -> str:
    """通过 MiniMax 异步生成视频；本工具等待 video_gen_tool_wait_seconds（默认 180s）后返回链接或待查询的 task_id。"""
    if not isinstance(duration, int) or not 4 <= duration <= 15:
        return tool_error("duration must be an integer between 4 and 15 seconds")
    if resolution not in ("512P", "768P", "1080P", "2K"):
        return tool_error("resolution must be one of 512P / 768P / 1080P / 2K")

    if subject == "self":
        if user_id is None:
            return tool_error("生成自己的形象需要用户上下文")
        try:
            visual = await load_self_visual_context(user_id)
            plan = apply_outfit_override(visual, outfit_override)
            first_frame_image = await prepare_self_video_reference(
                plan,
                user_id,
                first_frame_image,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )
        except (AvatarGenerationError, VisualReasoningError, ImageGenerationError) as e:
            return tool_error(str(e))
        prompt = (
            SELF_VIDEO_REFERENCE_TEMPLATE.format(
                prompt=prompt,
                outfit=SELF_VIDEO_KEEP_OUTFIT,
            )
            + "\n"
            + render_character_identity(visual.identity)
        )
    elif first_frame_image:
        prompt = IMAGE_ANIMATION_TEMPLATE.format(prompt=prompt)

    try:
        if user_id is not None:
            async with SESSION_LOCAL() as db:
                job = await enqueue_video_job(
                    db,
                    user_id=user_id,
                    session_id=parent_session_id,
                    prompt=prompt,
                    duration=duration,
                    resolution=resolution,
                    first_frame_image=first_frame_image,
                    aspect_ratio=aspect_ratio,
                    identity_reference_path=visual.reference_path if subject == "self" else None,
                    identity=visual.identity if subject == "self" else None,
                )
        else:
            return tool_error("视频生成服务需要用户上下文")
    except MissingLlmConfigError:
        return tool_error("视频生成服务未配置")
    except Exception as e:
        logger.exception("video_generation_tool submit failed")
        return tool_error(str(e))

    if job.status == "result_unknown":
        return json.dumps(
            {
                "success": False,
                "status": "result_unknown",
                "task_id": str(job.id),
                "error": job.error_message,
                "retry_safe": False,
            },
            ensure_ascii=False,
        )

    # 限时等待：轮询 DB 行直到终态或截止。
    deadline = utc_now() + timedelta(seconds=SETTINGS.video_gen_tool_wait_seconds)
    interval = min(SETTINGS.video_gen_poll_interval_seconds, 5.0)
    while utc_now() < deadline:
        await asyncio.sleep(interval)
        async with SESSION_LOCAL() as db:
            row = await get_job(db, job.id, user_id)
        if row is None:
            return tool_error("video job disappeared")
        if row.status == "succeeded":
            logger.info("video_generation_tool succeeded", extra={"job_id": job.id})
            return json.dumps(
                {
                    "success": True,
                    "url": row.video_url,
                    "task_id": str(job.id),
                    **({"warning": row.error_message} if row.error_message else {}),
                },
                ensure_ascii=False,
            )
        if row.status in ("failed", "result_unknown"):
            return tool_error(row.error_message or "video generation failed")

    # 已超时——任务在后台继续，模型可后续查询。
    logger.info("video_generation_tool timed out, job continues", extra={"job_id": job.id})
    return json.dumps(
        {
            "success": True,
            "pending": True,
            "task_id": str(job.id),
            "hint": "视频仍在生成中，请稍后用 video_generate_status 查询结果",
        },
        ensure_ascii=False,
    )


async def video_generate_status_tool(task_id: int, user_id: int | None = None, **_) -> str:
    """查询之前提交的 video 生成任务状态。"""
    if user_id is None:
        return tool_error("需要用户上下文")
    try:
        job_id = int(task_id)
    except (TypeError, ValueError):
        return tool_error("task_id must be an integer")
    async with SESSION_LOCAL() as db:
        row = await get_job(db, job_id, user_id)
    if row is None:
        return tool_error("video job not found")
    payload = {"task_id": str(row.id), "status": row.status}
    if row.status == "succeeded":
        payload["url"] = row.video_url
        if row.error_message:
            payload["warning"] = row.error_message
    elif row.status == "failed":
        payload["error"] = row.error_message
    elif row.status == "result_unknown":
        payload.update({"error": row.error_message, "retry_safe": False})
    return json.dumps(payload, ensure_ascii=False)


VIDEO_GENERATION_SCHEMA = {
    "name": "video_generate",
    "description": VIDEO_GENERATION_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": VIDEO_GENERATION_PARAM_DESCS["prompt"]},
            "subject": {
                "type": "string",
                "enum": ["self"],
                "description": VIDEO_GENERATION_PARAM_DESCS["subject"],
            },
            "duration": {
                "type": "integer",
                "minimum": 4,
                "maximum": 15,
                "description": VIDEO_GENERATION_PARAM_DESCS["duration"],
            },
            "resolution": {
                "type": "string",
                "enum": ["512P", "768P", "1080P", "2K"],
                "description": VIDEO_GENERATION_PARAM_DESCS["resolution"],
            },
            "first_frame_image": {
                "type": "string",
                "description": VIDEO_GENERATION_PARAM_DESCS["first_frame_image"],
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
                "description": VIDEO_GENERATION_PARAM_DESCS["aspect_ratio"],
            },
            "outfit_override": {
                "type": "string",
                "description": VIDEO_GENERATION_PARAM_DESCS["outfit_override"],
            },
        },
        "required": ["prompt"],
    },
}

VIDEO_STATUS_SCHEMA = {
    "name": "video_generate_status",
    "description": VIDEO_STATUS_DESC,
    "parameters": {
        "type": "object",
        "properties": {"task_id": {"type": "integer", "description": VIDEO_STATUS_PARAM_DESCS["task_id"]}},
        "required": ["task_id"],
    },
}


def register(registry) -> None:
    REGISTRY.register("video_generate", VIDEO_GENERATION_SCHEMA, video_generation_tool)
    REGISTRY.register("video_generate_status", VIDEO_STATUS_SCHEMA, video_generate_status_tool)
