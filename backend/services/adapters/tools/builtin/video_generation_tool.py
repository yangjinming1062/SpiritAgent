import asyncio
import json
from datetime import timedelta

from components import SESSION_LOCAL, SETTINGS, get_logger, tool_error, utc_now

from services.application.generation import (
    AvatarGenerationError,
    enqueue_video_job,
    get_job,
    resolve_self_reference_data_uri,
)
from services.infrastructure.llm import MissingLlmConfigError
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
    **_,
) -> str:
    """通过 MiniMax 异步生成视频；本工具等待 video_gen_tool_wait_seconds（默认 180s）后返回链接或待查询的 task_id。"""
    if subject == "self" and not first_frame_image:
        if user_id is None:
            return tool_error("生成自己的形象需要用户上下文")
        try:
            first_frame_image = await resolve_self_reference_data_uri(user_id)
        except AvatarGenerationError as e:
            return tool_error(str(e))
    if not isinstance(duration, int) or not 4 <= duration <= 15:
        return tool_error("duration must be an integer between 4 and 15 seconds")
    if resolution not in ("512P", "768P", "1080P", "2K"):
        return tool_error("resolution must be one of 512P / 768P / 1080P / 2K")

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
                    model=None,
                    aspect_ratio=aspect_ratio,
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
            return json.dumps({"success": True, "url": row.video_url, "task_id": str(job.id)}, ensure_ascii=False)
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
    elif row.status == "failed":
        payload["error"] = row.error_message
    elif row.status == "result_unknown":
        payload.update({"error": row.error_message, "retry_safe": False})
    return json.dumps(payload, ensure_ascii=False)


VIDEO_GENERATION_SCHEMA = {
    "name": "video_generate",
    "description": "Generate a short video from a text prompt (and optionally a first-frame image). Returns the video URL on success, or a pending marker with a task_id for long jobs. Default model: MiniMax-Hailuo-2.3 (v1 API: duration 6 or 10 seconds, resolution 512P/768P/1080P). If the deployment is configured with MiniMax-H3 (v2, paid plan) the limits are instead 4-15 seconds and 768P/2K, and aspect_ratio is mandatory for text-to-video. The provider URL is short-lived — we download and host locally, so the returned URL stays usable.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Describe the video content."},
            "subject": {
                "type": "string",
                "enum": ["self"],
                "description": "Set to 'self' when the video depicts YOU (the companion). The platform injects your canonical seed image as the first frame automatically; do NOT describe your own appearance from memory. Ignored when first_frame_image is set explicitly.",
            },
            "duration": {
                "type": "integer",
                "minimum": 4,
                "maximum": 15,
                "description": "Clip length in seconds. MiniMax-Hailuo (default): must be 6 or 10. MiniMax-H3: any integer 4-15.",
            },
            "resolution": {
                "type": "string",
                "enum": ["512P", "768P", "1080P", "2K"],
                "description": "Output resolution. MiniMax-Hailuo (default): 512P/768P/1080P. MiniMax-H3: 768P/2K.",
            },
            "first_frame_image": {
                "type": "string",
                "description": "Public URL or data URL of the first frame (i2v mode). When set, the provider derives the aspect ratio from the image; aspect_ratio is ignored.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
                "description": "Output aspect ratio. Ignored when first_frame_image is set (i2v). Required for text-to-video on MiniMax-H3; optional on MiniMax-Hailuo.",
            },
        },
        "required": ["prompt"],
    },
}

VIDEO_STATUS_SCHEMA = {
    "name": "video_generate_status",
    "description": "Check the status of a previously-submitted video_generate task. Returns status plus url (on success) or error (on failure).",
    "parameters": {
        "type": "object",
        "properties": {"task_id": {"type": "integer", "description": "The task_id returned by video_generate."}},
        "required": ["task_id"],
    },
}


def register(registry) -> None:
    REGISTRY.register("video_generate", VIDEO_GENERATION_SCHEMA, video_generation_tool)
    REGISTRY.register("video_generate_status", VIDEO_STATUS_SCHEMA, video_generate_status_tool)
