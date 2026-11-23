import asyncio
import json

from features.appearance.avatar_service import AvatarGenerationError
from features.appearance.visual_identity import (
    build_plain_video_prompt,
    build_self_video_inputs,
    load_self_visual_context,
)
from features.conversation.reply_media import apply_video_status, require_video_source
from features.media.contracts import VideoJobSnapshot, video_delivery_error
from features.media.identity_contracts import CharacterReferenceRole
from features.media.prompts import VIDEO_REFERENCE_GUIDANCE
from features.media.queries import chat_video_by_media_id, get_job
from features.media.turn_state import MediaArtifact, MediaTurnState
from features.media.video_jobs import enqueue_video_job, ensure_video_capability
from features.visuals.queries import prompt_presentation_snapshot
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.prompt_engineer import VisualReasoningError
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.api_ids import ResourceId
from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.database import SESSION_LOCAL
from infrastructure.hashing import sha256_hex
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.text import tool_error
from runtime.tools.prompts import (
    VIDEO_GENERATION_DESC,
    VIDEO_GENERATION_PARAM_DESCS,
    VIDEO_STATUS_DESC,
    VIDEO_STATUS_PARAM_DESCS,
)
from runtime.tools.registry import ToolsRegistry

logger = get_logger(__name__)

_MIN_DURATION_SECONDS = 4
_MAX_DURATION_SECONDS = 15
_DURATIONS = range(_MIN_DURATION_SECONDS, _MAX_DURATION_SECONDS + 1)
_RESOLUTIONS = frozenset({"512P", "768P", "1080P", "2K"})
_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
_SUBMIT_UNKNOWN_ERROR = "视频提交结果未核实，请勿重复提交"
_STRUCTURED_DELIVERY_HINT = (
    "Deliver each requested clip in its own video bubble. A pending card updates automatically when ready."
)


def _video_reference(reference: str, user_id: ResourceId) -> str | None:
    """保留本人的正式参考路径，外部输入由视频任务统一转正。"""
    if reference.startswith(("data:image/", "http://", "https://")):
        return reference
    parsed = parse_companion_asset_path(reference)
    if parsed is None or parsed[0] != user_id:
        return None
    resolved = resolve_asset_reference(reference)
    return reference if resolved is not None and resolved[1].startswith("image/") else None


async def _submit_video(
    prompt: str,
    duration: int,
    resolution: str,
    reference_image: str | None,
    aspect_ratio: str,
    *,
    turn: MediaTurnState,
    media_id: str,
    subject: str | None,
    outfit_override: str | None,
) -> tuple[VideoJobSnapshot, bool]:
    """提交已校验请求并返回真实任务状态；成品由后台沿原句柄查询及交付。"""
    goal = "\n".join(filter(None, (prompt, outfit_override)))
    visual_plan = None
    identity_reference_role: CharacterReferenceRole = "portrait"
    references = (reference_image,) if reference_image else ()
    reference_guidance = ""
    if subject == "self":
        presentation = await prompt_presentation_snapshot(turn.user_id)
        visual_plan = await load_self_visual_context(turn.user_id, presentation, outfit_override=outfit_override)
        inputs = await build_self_video_inputs(visual_plan, reference_image)
        reference_guidance, references = inputs.reference_guidance, inputs.references
        identity_reference_role = inputs.identity_reference_role
    elif references:
        reference_guidance = VIDEO_REFERENCE_GUIDANCE
    else:
        prompt = build_plain_video_prompt(prompt)

    async with SESSION_LOCAL() as db:
        await require_video_source(db, turn.user_id, turn.session_id, turn.source_message_id)
        return await enqueue_video_job(
            db,
            user_id=turn.user_id,
            session_id=turn.session_id,
            source_message_id=turn.source_message_id,
            request_scope=turn.request_scope,
            asset_directory=turn.asset_directory,
            prompt=prompt,
            goal=goal,
            duration=duration,
            resolution=resolution,
            reference_images=references,
            reference_guidance=reference_guidance,
            performance_context=visual_plan.context.performance_context if visual_plan else "",
            portrait_reference=visual_plan.context.portrait_path if visual_plan else None,
            aspect_ratio=aspect_ratio,
            identity_reference_path=visual_plan.context.fullbody_path if visual_plan is not None else None,
            identity=visual_plan.context.identity if visual_plan is not None else None,
            identity_reference_role=identity_reference_role,
            media_id=media_id,
            task=AiTaskKey(turn.task),
        )


def _video_result(job: VideoJobSnapshot, structured_reply: bool) -> dict[str, object]:
    task_id = str(job.id)
    if job.status == "result_unknown":
        return {
            "success": False,
            "task_id": task_id,
            "status": job.status,
            "retry_safe": False,
            "error": video_delivery_error(job.status),
        }
    if error := video_delivery_error(job.status):
        return {"success": False, "task_id": task_id, "error": error}
    if job.status == "succeeded":
        return {
            "success": True,
            "url": job.video_url,
            "task_id": task_id,
            **({"warning": job.error_message} if job.error_message else {}),
        }
    return {
        "success": True,
        "pending": True,
        "task_id": task_id,
        "video_queue": job.video_queue.model_dump(mode="json") if job.video_queue else None,
        **({} if structured_reply else {"hint": "视频仍在生成中，请稍后用 video_generate_status 查询结果"}),
    }


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
        return tool_error(f"请提供非空视频描述和 {_MIN_DURATION_SECONDS} 至 {_MAX_DURATION_SECONDS} 秒的时长")
    if not isinstance(resolution, str) or resolution not in _RESOLUTIONS:
        return tool_error("视频分辨率无效")
    if aspect_ratio and aspect_ratio not in _ASPECT_RATIOS:
        return tool_error("视频画幅无效")
    if subject not in (None, "self") or (outfit_override and subject != "self"):
        return tool_error("出镜角色使用 subject='self'，造型覆盖仅用于本次角色出镜")
    if outfit_override is not None and not isinstance(outfit_override, str):
        return tool_error("造型描述须为文字")
    if reference_image is not None and not isinstance(reference_image, str):
        return tool_error("reference_image 须为图片地址")
    prompt = prompt.strip()
    outfit_override = (outfit_override or "").strip() or None
    reference_image = (reference_image or "").strip() or None
    aspect_ratio = aspect_ratio or "9:16"
    media_id = sha256_hex(
        json.dumps(
            [
                media_turn.user_id,
                media_turn.session_id,
                media_turn.request_scope,
                prompt,
                duration,
                resolution,
                reference_image,
                aspect_ratio,
                subject,
                outfit_override,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    try:
        async with SESSION_LOCAL() as db:
            job = await chat_video_by_media_id(
                db, media_turn.user_id, media_turn.session_id, media_turn.request_scope, media_id
            )
    except RequestError as exc:
        return tool_error(exc.message)
    if job is not None:
        return _remember_job(media_turn, job, media_id, reused=True)
    if prior := media_turn.artifacts.get(media_id):
        if prior.job_id is not None:
            return tool_error("原视频任务已不可用")
        return json.dumps(
            {
                "success": False,
                "status": prior.status,
                "error": prior.error,
                "retry_safe": False,
                "media": [prior.tool_view()],
            },
            ensure_ascii=False,
        )

    if reference_image and await asyncio.to_thread(_video_reference, reference_image, media_turn.user_id) is None:
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
    artifact = MediaArtifact(media_id, "video", media_id, "pending")
    try:
        job, created = await _submit_video(
            prompt,
            duration,
            resolution,
            reference_image,
            aspect_ratio,
            turn=media_turn,
            subject=subject,
            outfit_override=outfit_override,
            media_id=media_id,
        )
    except (AvatarGenerationError, VisualReasoningError, MissingLlmConfigError, RequestError) as exc:
        return tool_error(str(exc))
    except Exception:
        # 任务行可能已提交、供应商可能已受理：按结果未知告知模型，不能当作失败重试。
        logger.exception("video_generation_tool submit outcome unknown")
        artifact.status, artifact.error = "result_unknown", _SUBMIT_UNKNOWN_ERROR
        result = {"success": False, "status": "result_unknown", "error": artifact.error, "retry_safe": False}
        media_turn.artifacts.setdefault(media_id, artifact)
        return json.dumps({**result, "media": [artifact.tool_view()]}, ensure_ascii=False)
    except BaseException:
        artifact.status, artifact.error = "result_unknown", _SUBMIT_UNKNOWN_ERROR
        media_turn.artifacts.setdefault(media_id, artifact)
        raise
    return _remember_job(media_turn, job, media_id, reused=not created)


def _remember_job(media_turn: MediaTurnState, job: VideoJobSnapshot, media_id: str, *, reused: bool) -> str:
    artifact = media_turn.artifacts.setdefault(media_id, MediaArtifact(media_id, "video", media_id, "pending"))
    artifact.job_id = job.id
    if job.revision >= artifact.media_revision:
        apply_video_status(artifact, job)
    media_turn.current_versions[media_id] = media_id
    if artifact.bound_message_id is None:
        media_turn.required_goals.add(media_id)
    result = _video_result(job, media_turn.structured_reply)
    if reused:
        result["reused"] = True
    if media_turn.structured_reply and result.get("success") and artifact.bound_message_id is None:
        result["next"] = _STRUCTURED_DELIVERY_HINT
    return json.dumps({**result, "media": [artifact.tool_view()]}, ensure_ascii=False)


async def video_generate_status_tool(
    task_id: ResourceId, *, user_id: ResourceId, media_turn: MediaTurnState, **_: object
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
        payload["error"] = video_delivery_error(row.status)
    elif row.status == "result_unknown":
        payload.update({"error": video_delivery_error(row.status), "retry_safe": False})
    if row.media_id:
        artifact = media_turn.artifacts.get(row.media_id) or MediaArtifact(
            row.media_id, "video", row.media_id, "pending", job_id=row.id
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
            "subject": {"type": "string", "enum": ["self"], "description": VIDEO_GENERATION_PARAM_DESCS["subject"]},
            "duration": {
                "type": "integer",
                "minimum": _MIN_DURATION_SECONDS,
                "maximum": _MAX_DURATION_SECONDS,
                "description": VIDEO_GENERATION_PARAM_DESCS["duration"],
            },
            "resolution": {
                "type": "string",
                "enum": ["512P", "768P", "1080P", "2K"],
                "description": VIDEO_GENERATION_PARAM_DESCS["resolution"],
            },
            "reference_image": {"type": "string", "description": VIDEO_GENERATION_PARAM_DESCS["reference_image"]},
            "aspect_ratio": {
                "type": "string",
                "enum": list(_ASPECT_RATIOS),
                "description": VIDEO_GENERATION_PARAM_DESCS["aspect_ratio"],
            },
            "outfit_override": {"type": "string", "description": VIDEO_GENERATION_PARAM_DESCS["outfit_override"]},
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
        "properties": {
            "task_id": {
                "type": "string",
                "pattern": "^[1-9][0-9]*$",
                "description": VIDEO_STATUS_PARAM_DESCS["task_id"],
            }
        },
        "required": ["task_id"],
    },
}


def register(registry: ToolsRegistry) -> None:
    registry.register(VIDEO_GENERATION_SCHEMA, video_generation_tool)
    registry.register(VIDEO_STATUS_SCHEMA, video_generate_status_tool)
