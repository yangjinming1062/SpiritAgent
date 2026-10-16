import asyncio
import json
from uuid import uuid4

from features.appearance.avatar_service import AvatarGenerationError
from features.appearance.visual_identity import (
    apply_outfit_override,
    build_plain_video_prompt,
    build_self_video_prompt,
    load_self_visual_context,
    self_video_references,
)
from features.conversation.reply_media import apply_video_status, require_video_source
from features.media.contracts import VideoJobSnapshot
from features.media.prompts import VIDEO_REFERENCE_TEMPLATE
from features.media.queries import get_job
from features.media.turn_state import MediaArtifact, MediaTurnState
from features.media.video_jobs import enqueue_video_job, ensure_video_capability
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.prompt_engineer import VisualReasoningError
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets import asset_store
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import tool_error
from runtime.tools.prompts import (
    VIDEO_GENERATION_DESC,
    VIDEO_GENERATION_PARAM_DESCS,
    VIDEO_STATUS_DESC,
    VIDEO_STATUS_PARAM_DESCS,
)
from runtime.tools.registry import ToolsRegistry

logger = get_logger(__name__)

_DURATIONS = range(4, 16)
_RESOLUTIONS = frozenset({"512P", "768P", "1080P", "2K"})
_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
_SUBMIT_UNKNOWN_ERROR = "视频提交结果未核实，请勿重复提交"
_STRUCTURED_DELIVERY_HINT = (
    "Put this media_id in a video bubble of your final reply; its card updates when the video is ready. "
    "The reply is not a tool call, so do not poll or submit again."
)


def _video_reference(reference: str, user_id: int) -> str | None:
    """本人资产路径读为 data URI；data URI 与 http(s) 地址原样交给供应商链，其余值无效。"""
    if reference.startswith(("data:image/", "http://", "https://")):
        return reference
    parsed = asset_store.parse_companion_asset_path(reference)
    if parsed is None or parsed[0] != user_id:
        return None
    return asset_store.read_asset_data_uri(reference)


def _result_unknown_payload(task_id: str, job: VideoJobSnapshot) -> dict[str, object]:
    return {
        "success": False,
        "status": "result_unknown",
        "task_id": task_id,
        "error": job.error_message,
        "retry_safe": False,
    }


async def _submit_video(
    prompt: str,
    duration: int,
    resolution: str,
    reference_image: str | None,
    aspect_ratio: str,
    *,
    user_id: int,
    task: AiTaskKey,
    parent_session_id: str,
    source_message_id: int | None,
    asset_directory: str,
    structured_reply: bool,
    media_id: str,
    subject: str | None,
    outfit_override: str | None,
) -> tuple[dict[str, object], VideoJobSnapshot | None]:
    """提交已校验请求并返回真实任务状态；成品由后台沿原句柄查询及交付。"""
    visual = None
    references = (reference_image,) if reference_image else ()
    if subject == "self":
        try:
            visual = await load_self_visual_context(user_id)
            plan = apply_outfit_override(visual, outfit_override)
            references = self_video_references(plan, reference_image)
        except (AvatarGenerationError, VisualReasoningError) as e:
            return {"success": False, "error": str(e)}, None
        prompt = build_self_video_prompt(plan, prompt, has_outfit_reference=len(references) > 1)
    elif references:
        prompt = VIDEO_REFERENCE_TEMPLATE.format(prompt=prompt)
    else:
        prompt = build_plain_video_prompt(prompt)

    try:
        async with SESSION_LOCAL() as db:
            await require_video_source(db, user_id, parent_session_id, source_message_id)
            job = await enqueue_video_job(
                db,
                user_id=user_id,
                session_id=parent_session_id,
                source_message_id=source_message_id,
                asset_directory=asset_directory,
                prompt=prompt,
                duration=duration,
                resolution=resolution,
                reference_images=references,
                aspect_ratio=aspect_ratio,
                identity_reference_path=visual.fullbody_path if visual is not None else None,
                identity=visual.identity if visual is not None else None,
                structured_reply=structured_reply,
                media_id=media_id,
                task=task,
            )
    except MissingLlmConfigError:
        # 任务行写入前抛出，确定未提交；其余异常可能发生在任务行提交、供应商受理之后，交给调用方按结果未知处理。
        return {"success": False, "error": "视频生成服务未配置"}, None

    task_id = str(job.id)
    if job.status == "result_unknown":
        return _result_unknown_payload(task_id, job), job

    if job.status == "failed":
        return {"success": False, "task_id": task_id, "error": job.error_message or "video generation failed"}, job
    if job.status == "succeeded":
        return {
            "success": True,
            "url": job.video_url,
            "task_id": task_id,
            **({"warning": job.error_message} if job.error_message else {}),
        }, job
    return {
        "success": True,
        "pending": True,
        "task_id": task_id,
        "video_queue": job.video_queue.model_dump(mode="json") if job.video_queue else None,
        **({} if structured_reply else {"hint": "视频仍在生成中，请稍后用 video_generate_status 查询结果"}),
    }, job


async def video_generation_tool(
    prompt: str,
    duration: int = 6,
    resolution: str = "768P",
    reference_image: str | None = None,
    aspect_ratio: str = "9:16",
    subject: str | None = None,
    outfit_override: str | None = None,
    *,
    media_turn: MediaTurnState,
    **_: object,
) -> str:
    if not isinstance(prompt, str) or not prompt.strip() or type(duration) is not int or duration not in _DURATIONS:
        return tool_error("请提供非空视频描述和 4 至 15 秒的时长")
    if resolution not in _RESOLUTIONS:
        return tool_error("视频分辨率无效")
    if aspect_ratio and aspect_ratio not in _ASPECT_RATIOS:
        return tool_error("视频画幅无效")
    if subject not in (None, "self") or (outfit_override and subject != "self"):
        return tool_error("出镜角色使用 subject='self'，造型覆盖仅用于本次角色出镜")
    if reference_image:
        # 模型只看得到产物的裸存储路径；在占用本轮视频名额前转为供应商可读的 data URI，无法读取时按参数错误返回。
        if not isinstance(reference_image, str):
            return tool_error("reference_image 须为图片地址")
        reference_image = await asyncio.to_thread(_video_reference, reference_image, media_turn.user_id)
        if reference_image is None:
            return tool_error("reference_image 须为本会话图片工具返回的地址，或可公开访问的 http(s) 图片地址")
    try:
        await ensure_video_capability(
            media_turn.user_id,
            reference_images=bool(reference_image) or subject == "self",
            duration=duration,
            resolution=resolution,
            allowed_durations=_DURATIONS,
            allowed_resolutions=_RESOLUTIONS,
            task=AiTaskKey(media_turn.task),
        )
    except MissingLlmConfigError as e:
        return tool_error(str(e))
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
        media_turn.mark_media_accepted()
        media_id = uuid4().hex
        artifact = MediaArtifact(media_id, "video", media_id, "pending")
        media_turn.artifacts[media_id] = artifact
        media_turn.current_versions[media_id] = media_id
    try:
        result, job = await _submit_video(
            prompt,
            duration,
            resolution,
            reference_image,
            aspect_ratio or "9:16",
            user_id=media_turn.user_id,
            task=AiTaskKey(media_turn.task),
            parent_session_id=media_turn.session_id,
            source_message_id=media_turn.source_message_id,
            asset_directory=media_turn.asset_directory,
            subject=subject,
            outfit_override=outfit_override,
            structured_reply=media_turn.structured_reply,
            media_id=media_id,
        )
    except Exception:
        # 任务行可能已提交、供应商可能已受理：按结果未知告知模型，不能当作失败重试。
        logger.exception("video_generation_tool submit outcome unknown")
        artifact.status, artifact.error = "result_unknown", _SUBMIT_UNKNOWN_ERROR
        result = {"success": False, "status": "result_unknown", "error": artifact.error, "retry_safe": False}
        return json.dumps({**result, "media": [artifact.tool_view()]}, ensure_ascii=False)
    except BaseException:
        artifact.status, artifact.error = "result_unknown", _SUBMIT_UNKNOWN_ERROR
        raise
    if job is None:
        artifact.status, artifact.error = "failed", str(result.get("error") or "视频未受理")
    else:
        artifact.job_id = job.id
        apply_video_status(artifact, job)
        media_turn.required_goals.add(media_id)
        if media_turn.structured_reply and result.get("success"):
            result = {**result, "next": _STRUCTURED_DELIVERY_HINT}
    return json.dumps({**result, "media": [artifact.tool_view()]}, ensure_ascii=False)


async def video_generate_status_tool(
    task_id: int,
    *,
    user_id: int,
    media_turn: MediaTurnState,
    **_: object,
) -> str:
    """查询之前提交的 video 生成任务状态。"""
    try:
        job_id = int(task_id)
    except (TypeError, ValueError):
        return tool_error("task_id must be an integer")
    async with SESSION_LOCAL() as db:
        row = await get_job(db, job_id, user_id)
    if row is None or row.session_id != media_turn.session_id:
        return tool_error("video job not found")
    payload: dict[str, object] = {"task_id": str(row.id), "status": row.status}
    payload["video_queue"] = row.video_queue.model_dump(mode="json") if row.video_queue else None
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
            "reference_image": {
                "type": "string",
                "description": VIDEO_GENERATION_PARAM_DESCS["reference_image"],
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(_ASPECT_RATIOS),
                "description": VIDEO_GENERATION_PARAM_DESCS["aspect_ratio"],
            },
            "outfit_override": {
                "type": "string",
                "description": VIDEO_GENERATION_PARAM_DESCS["outfit_override"],
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
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
