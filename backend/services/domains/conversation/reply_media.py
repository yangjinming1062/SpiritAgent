"""媒体引用归属、视频回复绑定和异步原位更新。"""

import json

from modules.conversation import CompanionReply, Conversation, MediaBubble, Message
from modules.media import VideoGenJob
from modules.ws import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MediaArtifact, MediaTurnState
from services.infrastructure.assets import asset_store

from .context_window import load_context_messages
from .reply_audio import client_reply_bubbles


def _read_tool_artifact(data: dict) -> MediaArtifact | None:
    try:
        bubble = MediaBubble.model_validate(
            {
                "type": data.get("type"),
                "media_id": data.get("media_id"),
                "goal_id": data.get("goal_id"),
                "status": data.get("status"),
                "url": data.get("url"),
                "error": data.get("error"),
                "job_id": data.get("task_id"),
            },
        )
    except ValueError:
        return None
    score = data.get("identity_score")
    return MediaArtifact(
        **bubble.model_dump(),
        identity_score=score if type(score) is int and 0 <= score <= 100 else None,
    )


async def load_media_turn(
    db: AsyncSession,
    conv: Conversation,
    *,
    structured_reply: bool,
    request: str,
) -> MediaTurnState:
    state = MediaTurnState(conv.user_id, str(conv.id), structured_reply, request)
    producer_calls: set[str] = set()
    for message in await load_context_messages(db, conv):
        if message.reply_json:
            reply = CompanionReply.model_validate_json(message.reply_json)
            for bubble in reply.bubbles:
                if isinstance(bubble, MediaBubble):
                    state.artifacts[bubble.media_id] = MediaArtifact(**bubble.model_dump())
        if message.role == "assistant" and message.tool_calls:
            for call in json.loads(message.tool_calls):
                if call.get("name") in {
                    "image_generate",
                    "image_regenerate",
                    "video_generate",
                    "video_generate_status",
                }:
                    producer_calls.add(call.get("call_id", ""))
        if message.role == "tool" and message.tool_call_id in producer_calls:
            try:
                result = json.loads(message.content or "")
            except ValueError:
                continue
            if not isinstance(result, dict):
                continue
            for entry in result.get("media", []):
                if isinstance(entry, dict) and (artifact := _read_tool_artifact(entry)):
                    state.artifacts.setdefault(artifact.media_id, artifact)
    # 未完成任务不能因历史压缩而失去去重依据。
    pending_jobs = await db.scalars(
        select(VideoGenJob).where(
            VideoGenJob.user_id == state.user_id,
            VideoGenJob.session_id == state.session_id,
            VideoGenJob.media_id.is_not(None),
            VideoGenJob.status.not_in(("succeeded", "failed", "result_unknown")),
        ),
    )
    for job in pending_jobs:
        artifact = MediaArtifact(job.media_id, "video", job.media_id, "pending", job_id=job.id)
        apply_video_status(artifact, job)
        state.artifacts[artifact.media_id] = artifact
    await refresh_video_media(db, state)
    for artifact in state.artifacts.values():
        if artifact.type == "video" and artifact.status == "pending":
            state.video_claimed = True
            state.current_versions[artifact.goal_id] = artifact.media_id
    return state


def apply_video_status(artifact: MediaArtifact, job: VideoGenJob) -> None:
    artifact.status = {"succeeded": "ready", "failed": "failed", "result_unknown": "result_unknown"}.get(
        job.status,
        "pending",
    )
    artifact.bound_message_id = job.reply_message_id
    artifact.url = job.video_url if artifact.status == "ready" else None
    artifact.error = job.error_message if artifact.status in {"failed", "result_unknown"} else None


async def refresh_video_media(db: AsyncSession, state: MediaTurnState) -> None:
    videos = [a for a in state.artifacts.values() if a.type == "video" and a.job_id is not None]
    if not videos:
        return
    jobs = {
        job.id: job
        for job in await db.scalars(
            select(VideoGenJob).where(
                VideoGenJob.id.in_([a.job_id for a in videos]),
                VideoGenJob.user_id == state.user_id,
                VideoGenJob.session_id == state.session_id,
            ),
        )
    }
    for artifact in videos:
        job = jobs.get(artifact.job_id)
        if job is None or job.media_id != artifact.media_id:
            artifact.status, artifact.url, artifact.error = "failed", None, "视频任务已不可用"
        else:
            apply_video_status(artifact, job)


def resolve_reply_media(state: MediaTurnState, media_id: str, media_type: str) -> MediaBubble:
    artifact = state.artifacts.get(media_id)
    if artifact is None or artifact.type != media_type:
        raise ValueError("Media reference is unknown or has the wrong type")
    if artifact.status == "pending" and artifact.bound_message_id is not None:
        raise ValueError("Pending video already has a delivery card; query its status without sending another")
    if artifact.type == "image" and artifact.status != "ready":
        raise ValueError("Images require a completed, available asset")
    if artifact.status == "ready":
        parsed = asset_store.parse_companion_asset_path(artifact.url or "")
        if parsed is None or parsed[0] != state.user_id or asset_store.resolve_companion_asset_path(*parsed) is None:
            raise ValueError("Media asset is unavailable or belongs to another user")
    return MediaBubble(
        type=artifact.type,
        media_id=artifact.media_id,
        goal_id=artifact.goal_id,
        status=artifact.status,
        url=artifact.url,
        error=artifact.error,
        job_id=artifact.job_id,
    )


async def bind_reply_videos(db: AsyncSession, message: Message, reply: CompanionReply, user_id: int) -> None:
    for bubble in sorted(
        (b for b in reply.bubbles if isinstance(b, MediaBubble) and b.job_id is not None),
        key=lambda b: b.job_id or 0,
    ):
        job = await db.scalar(select(VideoGenJob).where(VideoGenJob.id == bubble.job_id).with_for_update())
        if (
            job is None
            or job.user_id != user_id
            or job.session_id != str(message.conversation_id)
            or job.media_id != bubble.media_id
        ):
            raise ValueError("Video task does not belong to this reply")
        if job.reply_message_id not in (None, message.id) and job.status not in {
            "succeeded",
            "failed",
            "result_unknown",
        }:
            raise ValueError("Pending video already belongs to another reply")
        if job.reply_message_id is None and job.structured_reply:
            job.reply_message_id = message.id
        artifact = MediaArtifact(bubble.media_id, "video", bubble.goal_id, bubble.status, job_id=job.id)
        apply_video_status(artifact, job)
        bubble.status, bubble.url, bubble.error = artifact.status, artifact.url, artifact.error
    message.reply_json = reply.model_dump_json()


async def update_video_reply(db: AsyncSession, job: VideoGenJob) -> None:
    """调用方持有任务行锁；同事务保存终态和更新事件，消息删除后不补建。"""
    if job.reply_message_id is None:
        return
    message = await db.scalar(
        select(Message)
        .where(
            Message.id == job.reply_message_id,
            Message.conversation.has(Conversation.user_id == job.user_id),
        )
        .with_for_update(),
    )
    if message is None or message.reply_json is None:
        return
    reply = CompanionReply.model_validate_json(message.reply_json)
    for index, bubble in enumerate(reply.bubbles):
        if not isinstance(bubble, MediaBubble) or bubble.media_id != job.media_id or bubble.status != "pending":
            continue
        artifact = MediaArtifact(bubble.media_id, "video", bubble.goal_id, "pending", job_id=job.id)
        apply_video_status(artifact, job)
        if artifact.status == "pending":
            return
        bubble.status, bubble.url, bubble.error = artifact.status, artifact.url, artifact.error
        message.reply_json = reply.model_dump_json()
        emit_ws_event(
            db,
            user_id=job.user_id,
            event_type="message.media",
            payload={
                "session_id": str(message.conversation_id),
                "message_id": message.id,
                "media_id": bubble.media_id,
                "bubble_index": index,
                "bubble": client_reply_bubbles(reply)[index],
            },
        )
        return
