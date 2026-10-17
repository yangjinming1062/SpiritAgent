"""媒体引用归属、视频回复绑定和异步原位更新。"""

import contextlib
import json

from infrastructure.assets.asset_store import client_asset_url, resolve_companion_asset_path
from infrastructure.assets.paths import parse_companion_asset_path
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.context_window import load_context_messages
from features.conversation.main_conversation import MEDIA_FAILURE_SUBTYPE, MEDIA_STATUS_SUBTYPE
from features.conversation.models import Conversation, Message
from features.conversation.replies import CompanionReply, MediaBubble
from features.conversation.reply_audio import client_reply_bubbles
from features.conversation.snapshots import ConversationSnapshot
from features.media.contracts import VideoJobSnapshot
from features.media.queries import bind_video_reply, get_video_jobs, pending_conversation_video_jobs
from features.media.turn_state import MediaArtifact, MediaTurnState


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
    conv: Conversation | ConversationSnapshot,
    *,
    task: str,
    structured_reply: bool,
    request: str,
    retry_after_message_id: int | None = None,
) -> MediaTurnState:
    state = MediaTurnState(conv.user_id, str(conv.id), task, structured_reply, request)
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
                    if (
                        retry_after_message_id is not None
                        and message.id > retry_after_message_id
                        and (artifact.type == "video" or artifact.status == "ready")
                    ):
                        state.required_goals.add(artifact.goal_id)
                        state.current_versions[artifact.goal_id] = artifact.media_id
    # 未完成任务不能因历史压缩而失去去重依据。
    pending_jobs = await pending_conversation_video_jobs(db, state.user_id, state.session_id)
    for job in pending_jobs:
        if job.media_id is None:
            continue
        artifact = MediaArtifact(job.media_id, "video", job.media_id, "pending", job_id=job.id)
        apply_video_status(artifact, job)
        state.artifacts[artifact.media_id] = artifact
    await refresh_video_media(db, state)
    for artifact in state.artifacts.values():
        if artifact.type == "video" and artifact.status == "pending":
            state.video_claimed = True
            state.current_versions[artifact.goal_id] = artifact.media_id
    return state


def apply_video_status(artifact: MediaArtifact, job: VideoJobSnapshot) -> None:
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
    jobs = await get_video_jobs(db, state.user_id, [a.job_id for a in videos if a.job_id is not None])
    for artifact in videos:
        job = jobs.get(artifact.job_id)
        if (
            job is None
            or job.session_id != state.session_id
            or job.cleanup_requested_at is not None
            or job.media_id != artifact.media_id
        ):
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
        parsed = parse_companion_asset_path(artifact.url or "")
        if parsed is None or parsed[0] != state.user_id or resolve_companion_asset_path(*parsed) is None:
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
        if bubble.job_id is None:
            continue
        job = await bind_video_reply(
            db,
            user_id=user_id,
            session_id=str(message.conversation_id),
            job_id=bubble.job_id,
            media_id=bubble.media_id,
            message_id=message.id,
        )
        artifact = MediaArtifact(bubble.media_id, "video", bubble.goal_id, bubble.status, job_id=job.id)
        apply_video_status(artifact, job)
        bubble.status, bubble.url, bubble.error = artifact.status, artifact.url, artifact.error
    message.reply_json = reply.model_dump_json()


async def update_video_reply(db: AsyncSession, job: VideoJobSnapshot) -> None:
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


async def require_video_source(db: AsyncSession, user_id: int, session_id: str, source_message_id: int | None) -> None:
    """与历史修改共享源用户消息锁；调用方创建任务后才提交该事务。"""
    if source_message_id is None:
        return
    source = await db.scalar(
        select(Message)
        .where(
            Message.id == source_message_id,
            Message.role == "user",
            Message.conversation.has(Conversation.user_id == user_id),
            Message.conversation_id == int(session_id or "0"),
        )
        .with_for_update(),
    )
    if source is None:
        raise ValueError("Video source message is no longer available")


async def deliver_video_result(db: AsyncSession, job: VideoJobSnapshot) -> None:
    """媒体所有者持任务行锁；业务交付与任务终态、outbox 在同一事务提交。"""
    if job.structured_reply:
        await update_video_reply(db, job)
        return
    if not job.session_id:
        return
    if job.status != "succeeded":
        status_message: Message | None = None
        with contextlib.suppress(TypeError, ValueError):
            conversation = await db.get(Conversation, int(job.session_id))
            if conversation is not None and conversation.user_id == job.user_id:
                status_message = Message(
                    conversation_id=conversation.id,
                    role="system",
                    subtype=MEDIA_FAILURE_SUBTYPE,
                    content=f"[视频任务 {job.id} 未完成] {job.error_message}",
                )
                db.add(status_message)
                await db.flush()
        emit_ws_event(
            db,
            user_id=job.user_id,
            event_type="video_gen.failed",
            payload={
                "task_id": str(job.id),
                "error": job.error_message,
                "session_id": job.session_id,
                "status_message_id": status_message.id if status_message is not None else None,
                "status_text": status_message.content if status_message is not None else None,
            },
        )
        return
    if job.video_url is None:
        raise ValueError("Completed video has no asset")
    client_url = client_asset_url(job.video_url)
    with contextlib.suppress(TypeError, ValueError):
        conversation = await db.get(Conversation, int(job.session_id))
        if conversation is not None and conversation.user_id == job.user_id:
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role="system",
                    subtype=MEDIA_STATUS_SUBTYPE,
                    content=f"[视频已生成 task {job.id}] {client_url}",
                    media_json=json.dumps([{"type": "video", "url": job.video_url}], ensure_ascii=False),
                ),
            )
    emit_ws_event(
        db,
        user_id=job.user_id,
        event_type="video_gen.completed",
        payload={
            "task_id": str(job.id),
            "url": client_url,
            "media": [{"type": "video", "url": client_url}],
            "session_id": job.session_id,
            **({"warning": job.error_message} if job.error_message else {}),
        },
    )
