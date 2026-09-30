import asyncio
import base64
import json
from datetime import timedelta
from uuid import uuid4

from components import SESSION_LOCAL, SETTINGS, get_logger, tool_error, utc_now
from modules.media import VideoGenJob
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
from services.contracts import MediaArtifact, MediaTurnState
from services.domains.companion import render_character_identity
from services.domains.conversation import apply_video_status
from services.infrastructure.assets import asset_store
from services.infrastructure.llm import MissingLlmConfigError, VisualReasoningError
from services.infrastructure.tool_runtime import ToolsRegistry

logger = get_logger(__name__)


def _first_frame_reference(reference: str, user_id: int) -> str | None:
    """本人资产路径读为 data URI；data URI 与 http(s) 地址原样交给供应商链，其余值无效。"""
    if reference.startswith(("data:image/", "http://", "https://")):
        return reference
    parsed = asset_store.parse_companion_asset_path(reference)
    if parsed is None or parsed[0] != user_id:
        return None
    resolved = asset_store.resolve_companion_asset_path(*parsed)
    if resolved is None or not resolved[1].startswith("image/"):
        return None
    try:
        data = resolved[0].read_bytes()
    except OSError:
        return None
    return f"data:{resolved[1]};base64,{base64.b64encode(data).decode('ascii')}"


async def _submit_video(
    prompt: str,
    duration: int,
    resolution: str,
    first_frame_image: str | None,
    aspect_ratio: str | None,
    *,
    user_id: int,
    parent_session_id: str,
    structured_reply: bool,
    media_id: str,
    subject: str | None,
    outfit_override: str | None,
) -> tuple[dict[str, object], VideoGenJob | None]:
    """提交已校验请求，返回 (工具结果, 最近读取的任务行)；结构化回复直接交付任务，文本渠道有界等待。"""
    visual = None
    if subject == "self":
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
            return {"success": False, "error": str(e)}, None
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
                identity_reference_path=visual.reference_path if visual is not None else None,
                identity=visual.identity if visual is not None else None,
                structured_reply=structured_reply,
                media_id=media_id,
            )
    except MissingLlmConfigError:
        return {"success": False, "error": "视频生成服务未配置"}, None
    except Exception as e:
        logger.exception("video_generation_tool submit failed")
        return {"success": False, "error": str(e)}, None

    task_id = str(job.id)
    if job.status == "result_unknown":
        return {
            "success": False,
            "status": "result_unknown",
            "task_id": task_id,
            "error": job.error_message,
            "retry_safe": False,
        }, job

    if structured_reply:
        return {
            "success": True,
            "pending": job.status not in {"succeeded", "failed", "result_unknown"},
            "task_id": task_id,
        }, job

    # 限时等待：轮询 DB 行直到终态或截止。
    deadline = utc_now() + timedelta(seconds=SETTINGS.video_gen_tool_wait_seconds)
    interval = min(SETTINGS.video_gen_poll_interval_seconds, 5.0)
    while utc_now() < deadline:
        await asyncio.sleep(interval)
        async with SESSION_LOCAL() as db:
            row = await get_job(db, job.id, user_id)
        if row is None:
            return {"success": False, "error": "video job disappeared"}, None
        job = row
        if row.status == "succeeded":
            logger.info("video_generation_tool succeeded", extra={"job_id": job.id})
            return {
                "success": True,
                "url": row.video_url,
                "task_id": task_id,
                **({"warning": row.error_message} if row.error_message else {}),
            }, job
        if row.status in ("failed", "result_unknown"):
            return {"success": False, "task_id": task_id, "error": row.error_message or "video generation failed"}, job

    # 已超时——任务在后台继续，模型可后续查询。
    logger.info("video_generation_tool timed out, job continues", extra={"job_id": job.id})
    return {
        "success": True,
        "pending": True,
        "task_id": task_id,
        "hint": "视频仍在生成中，请稍后用 video_generate_status 查询结果",
    }, job


async def video_generation_tool(
    prompt: str,
    duration: int = 6,
    resolution: str = "768P",
    first_frame_image: str | None = None,
    aspect_ratio: str | None = None,
    subject: str | None = None,
    outfit_override: str | None = None,
    media_turn: MediaTurnState | None = None,
    **kwargs,
) -> str:
    if media_turn is None:
        return tool_error("视频生成需要会话上下文")
    if not isinstance(prompt, str) or not prompt.strip() or type(duration) is not int or not 4 <= duration <= 15:
        return tool_error("请提供非空视频描述和 4 至 15 秒的时长")
    if resolution not in {"512P", "768P", "1080P", "2K"}:
        return tool_error("视频分辨率无效")
    if first_frame_image:
        # 模型只看得到产物的裸存储路径；在占用本轮视频名额前转为供应商可读的 data URI，无法读取时按参数错误返回。
        first_frame_image = await asyncio.to_thread(_first_frame_reference, first_frame_image, media_turn.user_id)
        if first_frame_image is None:
            return tool_error("first_frame_image 须为本会话图片工具返回的地址，或可公开访问的 http(s) 图片地址")
    async with media_turn.lock:
        if media_turn.video_claimed:
            return json.dumps(
                {
                    "success": True,
                    "reused": True,
                    "media": [
                        a.tool_view()
                        for a in media_turn.artifacts.values()
                        if a.type == "video" and a.goal_id in media_turn.current_versions
                    ],
                },
                ensure_ascii=False,
            )
        media_turn.video_claimed = True
        media_id = uuid4().hex
        artifact = MediaArtifact(media_id, "video", media_id, "pending")
        media_turn.artifacts[media_id] = artifact
        media_turn.current_versions[media_id] = media_id
    try:
        result, job = await _submit_video(
            prompt,
            duration,
            resolution,
            first_frame_image,
            aspect_ratio,
            user_id=media_turn.user_id,
            parent_session_id=media_turn.session_id,
            subject=subject,
            outfit_override=outfit_override,
            structured_reply=media_turn.structured_reply,
            media_id=media_id,
        )
    except BaseException:
        artifact.status, artifact.error = "result_unknown", "视频提交结果未核实，请勿重复提交"
        raise
    if job is None:
        artifact.status, artifact.error = "failed", str(result.get("error") or "视频未受理")
    else:
        artifact.job_id = job.id
        apply_video_status(artifact, job)
        media_turn.required_goals.add(media_id)
    return json.dumps({**result, "media": [artifact.tool_view()]}, ensure_ascii=False)


async def video_generate_status_tool(
    task_id: int,
    user_id: int | None = None,
    media_turn: MediaTurnState | None = None,
    **_,
) -> str:
    """查询之前提交的 video 生成任务状态。"""
    if user_id is None:
        return tool_error("需要用户上下文")
    try:
        job_id = int(task_id)
    except (TypeError, ValueError):
        return tool_error("task_id must be an integer")
    async with SESSION_LOCAL() as db:
        row = await get_job(db, job_id, user_id)
    if row is None or media_turn is None or row.session_id != media_turn.session_id:
        return tool_error("video job not found")
    payload: dict[str, object] = {"task_id": str(row.id), "status": row.status}
    if row.status == "succeeded":
        payload["url"] = row.video_url
        if row.error_message:
            payload["warning"] = row.error_message
    elif row.status == "failed":
        payload["error"] = row.error_message
    elif row.status == "result_unknown":
        payload.update({"error": row.error_message, "retry_safe": False})
    if row.media_id:
        artifact = media_turn.artifacts.get(row.media_id) or MediaArtifact(
            row.media_id,
            "video",
            row.media_id,
            "pending",
            job_id=row.id,
        )
        apply_video_status(artifact, row)
        media_turn.artifacts[artifact.media_id] = artifact
        if not media_turn.structured_reply and artifact.status == "ready":
            media_turn.required_goals.add(artifact.goal_id)
        payload.update({"success": True, "media": [artifact.tool_view()]})
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


def register(registry: ToolsRegistry) -> None:
    registry.register(VIDEO_GENERATION_SCHEMA, video_generation_tool)
    registry.register(VIDEO_STATUS_SCHEMA, video_generate_status_tool)
