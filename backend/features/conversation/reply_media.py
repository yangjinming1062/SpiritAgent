"""媒体引用归属、视频回复绑定和异步原位更新。"""

import contextlib
import json
from copy import deepcopy

from infrastructure.assets.asset_store import client_asset_url, resolve_companion_asset_path
from infrastructure.assets.asset_store import normalize_asset_reference as asset_store_path
from infrastructure.assets.paths import parse_companion_asset_path
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.asset_bindings import flush_conversation, sync_message_assets
from features.conversation.context_window import load_context_messages
from features.conversation.main_conversation import MEDIA_FAILURE_SUBTYPE, MEDIA_STATUS_SUBTYPE
from features.conversation.models import Conversation, Message
from features.conversation.replies import CompanionReply, MediaBubble, MediaBubbleStatus, TextBubble
from features.conversation.reply_audio import client_reply_bubbles
from features.conversation.snapshots import ConversationSnapshot
from features.media.continuations import MediaWait
from features.media.contracts import VideoJobSnapshot
from features.media.image_jobs import (
    bind_image_reply,
    checked_image_result,
    conversation_image_jobs,
    record_image_delivery,
)
from features.media.queries import bind_video_reply, get_video_jobs, pending_conversation_video_jobs
from features.media.review_views import MediaJobView
from features.media.turn_state import ImagePlan, MediaArtifact, MediaInspection, MediaTurnState
from features.media.video_jobs import accept_checked_video


def _sync_bubble(
    bubble: MediaBubble, status: MediaBubbleStatus, url: str | None, error: str | None, revision: int
) -> None:
    """媒体状态只经此回填，避免气泡字段与任务快照分叉。"""

    bubble.status, bubble.url, bubble.error, bubble.media_revision = status, url, error, revision


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
            }
        )
    except ValueError:
        return None
    verdict = data.get("identity_verdict")
    return MediaArtifact(
        **bubble.model_dump(), identity_verdict=verdict if verdict in ("pass", "review", "fail") else None
    )


def _restore_image_artifacts(
    state: MediaTurnState, image_jobs: tuple[MediaJobView, ...], source_message_id: int | None
) -> None:
    """把持久图片任务还原成回合产物：槽位、当前版本、计划与已用预算。"""

    active_image_ids = {job.id for job in image_jobs}
    for artifact in state.artifacts.values():
        if artifact.type == "image" and artifact.job_id is not None and artifact.job_id not in active_image_ids:
            artifact.status, artifact.url, artifact.error = "failed", None, "原图片任务已失效"
    for image_job in image_jobs:
        frozen = image_job.image_request
        if frozen is None:
            continue
        current_request = (
            image_job.source_message_id == source_message_id
            and source_message_id is not None
            or frozen.request_scope == state.request_scope
        )
        for slot in image_job.slots:
            goal_id = frozen.goal_ids[slot.slot]
            artifact = MediaArtifact(
                slot.media_id,
                "image",
                goal_id,
                slot.status,
                url=asset_store_path(slot.url),
                error=slot.error,
                job_id=image_job.id,
                bound_message_id=int(image_job.bindings[slot.media_id])
                if slot.media_id in image_job.bindings
                else None,
                media_revision=slot.media_revision,
            )
            state.artifacts[artifact.media_id] = artifact
            if current_request:
                state.current_versions[goal_id] = artifact.media_id
                if frozen.parent_media_id is None:
                    state.plans[goal_id] = ImagePlan(
                        request=frozen.request,
                        prompt=frozen.prompt,
                        size=frozen.size,
                        subject=frozen.subject,
                        reference_image=frozen.reference_image,
                        secondary_reference_image=frozen.secondary_reference_image,
                        identity_json=frozen.identity_json,
                        identity_reference_mode=frozen.identity_reference_mode,
                    )
                else:
                    state.regenerated_goals.add(goal_id)
                if artifact.bound_message_id is None and artifact.status in {"ready", "pending", "review_pending"}:
                    state.required_goals.add(goal_id)
        if current_request and frozen.parent_media_id is None:
            state.image_requests[frozen.request] = frozen.media_ids
            state.image_budget_used += len(frozen.media_ids)
            state.image_round = state.tool_round
            state.mark_media_accepted()


async def load_media_turn(
    db: AsyncSession,
    conv: Conversation | ConversationSnapshot,
    *,
    task: str,
    structured_reply: bool,
    request: str,
    retry_after_message_id: int | None = None,
    source_message_id: int | None = None,
    request_scope: str | None = None,
) -> MediaTurnState:
    state = MediaTurnState(
        conv.user_id, str(conv.id), task, structured_reply, request, source_message_id=source_message_id
    )
    if request_scope:
        state.request_scope = request_scope
    producer_calls: set[str] = set()
    inspection_calls: set[str] = set()
    for message in await load_context_messages(db, conv):
        if message.reply_json:
            reply = CompanionReply.from_saved_snapshot(message.reply_json, message.content or "")
            for bubble in reply.bubbles:
                if isinstance(bubble, MediaBubble):
                    state.artifacts[bubble.media_id] = MediaArtifact(**bubble.model_dump())
        if message.role == "assistant" and message.tool_calls:
            for call in deepcopy(message.tool_calls):
                if call.get("name") in {
                    "image_generate",
                    "image_regenerate",
                    "video_generate",
                    "video_generate_status",
                }:
                    producer_calls.add(call.get("call_id", ""))
                if call.get("name") == "media_inspect":
                    inspection_calls.add(call.get("call_id", ""))
        if message.role == "tool" and message.tool_call_id in inspection_calls:
            try:
                inspected = json.loads(message.content or "")
                if (
                    isinstance(inspected, dict)
                    and inspected.get("verdict") in {"pass", "revise", "unavailable"}
                    and isinstance(inspected.get("media_id"), str)
                    and isinstance(inspected.get("inspection_id"), str)
                    and isinstance(inspected.get("issues"), list)
                ):
                    state.inspections[inspected["media_id"]] = MediaInspection(
                        inspected["inspection_id"],
                        inspected["media_id"],
                        inspected["verdict"],
                        tuple(issue for issue in inspected["issues"] if isinstance(issue, str)),
                    )
            except ValueError:
                pass
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
    _restore_image_artifacts(
        state, await conversation_image_jobs(db, state.user_id, state.session_id), source_message_id
    )
    # 未完成任务不能因历史压缩而失去去重依据。
    pending_jobs = await pending_conversation_video_jobs(db, state.user_id, state.session_id, state.request_scope)
    for job in pending_jobs:
        if job.media_id is None:
            continue
        artifact = MediaArtifact(job.media_id, "video", job.media_id, "pending", job_id=job.id)
        apply_video_status(artifact, job)
        state.video_claimed = True
        if artifact.bound_message_id is None:
            state.required_goals.add(artifact.goal_id)
        state.current_versions[artifact.goal_id] = artifact.media_id
        state.artifacts[artifact.media_id] = artifact
    await refresh_video_media(db, state)
    return state


def apply_video_status(artifact: MediaArtifact, job: VideoJobSnapshot) -> None:
    artifact.status = {
        "succeeded": "ready",
        "failed": "failed",
        "result_unknown": "result_unknown",
        "review_pending": "review_pending",
        "confirmation": "review_pending",
    }.get(job.status, "pending")
    artifact.bound_message_id = job.reply_message_id
    artifact.media_revision = job.revision
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
    if artifact.status in {"pending", "review_pending"} and artifact.bound_message_id is not None:
        raise ValueError("Pending video already has a delivery card; query its status without sending another")
    if artifact.type == "image" and artifact.status != "ready" and artifact.job_id is None:
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
        job_id=None if artifact.status == "ready" and artifact.bound_message_id is not None else artifact.job_id,
        media_revision=artifact.media_revision,
    )


async def bind_reply_videos(db: AsyncSession, message: Message, reply: CompanionReply, user_id: int) -> None:
    for bubble in sorted(
        (b for b in reply.bubbles if isinstance(b, MediaBubble) and b.job_id is not None), key=lambda b: b.job_id or 0
    ):
        if bubble.job_id is None:
            continue
        if bubble.type == "image":
            result = await bind_image_reply(
                db, user_id, str(message.conversation_id), bubble.job_id, bubble.media_id, message.id
            )
            slot = next(entry for entry in result.slots if entry.media_id == bubble.media_id)
            bubble.status, bubble.url, bubble.error, bubble.media_revision = (
                slot.status,
                asset_store_path(slot.url) or None,
                slot.error,
                slot.media_revision,
            )
            continue
        job = await bind_video_reply(
            db,
            user_id=user_id,
            session_id=str(message.conversation_id),
            job_id=bubble.job_id,
            media_id=bubble.media_id,
            message_id=message.id,
        )
        if job.status == "confirmation":
            await accept_checked_video(db, user_id, job.id)
            job = (await get_video_jobs(db, user_id, (job.id,)))[job.id]
        artifact = MediaArtifact(bubble.media_id, "video", bubble.goal_id, bubble.status, job_id=job.id)
        apply_video_status(artifact, job)
        _sync_bubble(bubble, artifact.status, artifact.url, artifact.error, artifact.media_revision)
    message.reply_json = reply.model_dump(mode="json")
    await sync_message_assets(db)


async def update_video_reply(db: AsyncSession, job: VideoJobSnapshot) -> None:
    """调用方持有任务行锁；同事务保存终态和更新事件，消息删除后不补建。"""
    if job.reply_message_id is None:
        return
    message = await db.scalar(
        select(Message)
        .where(Message.id == job.reply_message_id, Message.conversation.has(Conversation.user_id == job.user_id))
        .with_for_update()
    )
    if message is None or message.reply_json is None:
        return
    reply = CompanionReply.from_saved_snapshot(message.reply_json, message.content or "")
    for index, bubble in enumerate(reply.bubbles):
        if (
            not isinstance(bubble, MediaBubble)
            or bubble.media_id != job.media_id
            or bubble.job_id != job.id
            or bubble.media_revision >= job.revision
            or bubble.status not in {"pending", "review_pending", "result_unknown"}
            and not (
                bubble.status == "failed" and job.status == "succeeded" and job.review and job.review.human_approval
            )
        ):
            continue
        artifact = MediaArtifact(bubble.media_id, "video", bubble.goal_id, "pending", job_id=job.id)
        apply_video_status(artifact, job)
        _sync_bubble(bubble, artifact.status, artifact.url, artifact.error, artifact.media_revision)
        message.reply_json = reply.model_dump(mode="json")
        await sync_message_assets(db)
        if message.call_record_id is not None:
            return
        emit_ws_event(
            db,
            user_id=job.user_id,
            event_type="message.media",
            payload={
                "session_id": str(message.conversation_id),
                "message_id": message.id,
                "media_id": bubble.media_id,
                "media_revision": bubble.media_revision,
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
        .with_for_update()
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
    if job.status not in {"succeeded", "failed", "result_unknown", "discarded"}:
        return
    if job.status != "succeeded":
        status_message: Message | None = None
        with contextlib.suppress(TypeError, ValueError):
            conversation = await db.get(Conversation, int(job.session_id))
            if conversation is not None and conversation.user_id == job.user_id:
                status_message = Message(
                    user_id=conversation.user_id,
                    conversation_id=conversation.id,
                    role="system",
                    subtype=MEDIA_FAILURE_SUBTYPE,
                    content=f"[视频任务 {job.id} 未完成] {job.error_message}",
                )
                db.add(status_message)
                await flush_conversation(db)
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
                    user_id=conversation.user_id,
                    conversation_id=conversation.id,
                    role="system",
                    subtype=MEDIA_STATUS_SUBTYPE,
                    content=f"[视频已生成 task {job.id}] {client_url}",
                    media_json=[{"type": "video", "url": job.video_url}],
                )
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


async def deliver_image_job_result(db: AsyncSession, job: MediaJobView) -> None:
    if job.user_id is None or not job.session_id:
        return
    for slot in job.slots:
        message_id = job.bindings.get(slot.media_id)
        if not message_id:
            continue
        message = await db.scalar(
            select(Message)
            .where(
                Message.id == int(message_id),
                Message.conversation_id == int(job.session_id),
                Message.user_id == job.user_id,
            )
            .with_for_update()
        )
        if message is None or not message.reply_json:
            continue
        reply = CompanionReply.from_saved_snapshot(message.reply_json, message.content or "")
        for index, bubble in enumerate(reply.bubbles):
            if (
                not isinstance(bubble, MediaBubble)
                or bubble.media_id != slot.media_id
                or bubble.job_id != job.id
                or bubble.status == "ready"
                or bubble.media_revision >= slot.media_revision
            ):
                continue
            revision = (
                await record_image_delivery(db, job.user_id, job.id, slot.media_id)
                if slot.status == "ready"
                else slot.media_revision
            )
            _sync_bubble(bubble, slot.status, asset_store_path(slot.url) or None, slot.error, revision)
            message.reply_json = reply.model_dump(mode="json")
            await sync_message_assets(db)
            if message.call_record_id is not None:
                continue
            emit_ws_event(
                db,
                user_id=job.user_id,
                event_type="message.media",
                payload={
                    "session_id": job.session_id,
                    "message_id": message.id,
                    "media_id": slot.media_id,
                    "media_revision": revision,
                    "bubble_index": index,
                    "bubble": client_reply_bubbles(reply)[index],
                },
            )


async def continue_chat_image(db: AsyncSession, wait: MediaWait) -> dict | None:
    job = await checked_image_result(db, wait.user_id, wait.owner_job_id)
    if job is None:
        return None
    slot = job.slots[wait.context_json["slot"]]
    if slot.status != "ready" or slot.media_id not in job.bindings:
        return None
    await deliver_image_job_result(db, job)
    return {"media_id": slot.media_id, "message_id": job.bindings[slot.media_id]}


async def continue_chat_video(db: AsyncSession, wait: MediaWait) -> dict | None:
    jobs = await get_video_jobs(db, wait.user_id, (wait.owner_job_id,))
    current = jobs.get(wait.owner_job_id)
    if current is None or current.structured_reply and current.reply_message_id is None:
        return None
    if not await accept_checked_video(db, wait.user_id, wait.owner_job_id):
        return None
    job = await get_video_jobs(db, wait.user_id, (wait.owner_job_id,))
    result = job.get(wait.owner_job_id)
    return (
        {"job_id": str(wait.owner_job_id)}
        if result and (not result.structured_reply or result.reply_message_id is not None)
        else None
    )


async def plain_media_delivery_reply(
    db: AsyncSession, user_id: int, session_id: str, text: str, request_scope: str
) -> CompanionReply | None:
    bubbles: list[TextBubble | MediaBubble] = [TextBubble(type="text", text=text)] if text.strip() else []
    for job in await conversation_image_jobs(db, user_id, session_id):
        if job.image_request is None or job.image_request.request_scope != request_scope:
            continue
        for slot in job.slots:
            if slot.media_id in job.bindings:
                continue
            bubbles.append(
                MediaBubble(
                    type="image",
                    media_id=slot.media_id,
                    goal_id=job.image_request.goal_ids[slot.slot],
                    status=slot.status,
                    url=asset_store_path(slot.url) or None,
                    error=slot.error,
                    job_id=job.id,
                    media_revision=slot.media_revision,
                )
            )
    for job in await pending_conversation_video_jobs(db, user_id, session_id, request_scope):
        if job.reply_message_id is not None or job.media_id is None:
            continue
        artifact = MediaArtifact(job.media_id, "video", job.media_id, "pending", job_id=job.id)
        apply_video_status(artifact, job)
        bubbles.append(
            MediaBubble(
                type="video",
                media_id=job.media_id,
                goal_id=job.media_id,
                status=artifact.status,
                url=artifact.url,
                error=artifact.error,
                job_id=job.id,
                media_revision=job.revision,
            )
        )
    return CompanionReply(bubbles=bubbles) if any(isinstance(bubble, MediaBubble) for bubble in bubbles) else None
