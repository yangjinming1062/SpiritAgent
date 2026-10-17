import contextlib
from collections.abc import AsyncIterator
from typing import Any

from features.conversation.attachments import (
    audio_file_id_from_url,
    resolve_audio_file,
    resolve_video_file,
    video_file_id_from_url,
)
from features.conversation.request_contracts import ChatAttachment
from features.conversation.session_operations import ConversationNotFoundError, get_conversation
from features.conversation.snapshots import ConversationSnapshot
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import UserLlmConfig, resolve_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.constants import (
    ATTACHMENT_DATA_URL_MAX_CHARS,
    ATTACHMENT_TYPE_AUDIO,
    ATTACHMENT_TYPE_IMAGE,
    ATTACHMENT_TYPE_VIDEO,
)
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from runtime.conversation import RuntimeSession
from runtime.turn_ownership import conversation_lock


@contextlib.asynccontextmanager
async def conversation_history_lock(session_id: str) -> AsyncIterator[None]:
    lock = conversation_lock(session_id)
    if lock.locked():
        raise RequestError(409, "当前会话正在执行任务，请先停止任务", code="turn_busy")
    async with lock:
        yield


async def _require_owned_conv(user_id: int, session_id: str) -> ConversationSnapshot:
    try:
        return await get_conversation(user_id, session_id)
    except ConversationNotFoundError as exc:
        raise RequestError(404, str(exc)) from exc


async def _resolve_llm_config(user_id: int, task: AiTaskKey) -> UserLlmConfig:
    """每次读取用户任务链，使配置变更对后续业务操作即时生效。"""
    async with SESSION_LOCAL() as db:
        return await resolve_user_llm_config(db, user_id, task)


def _reject_read_only_session(runtime: RuntimeSession) -> None:
    """自动化历史只由任务执行入口写入。"""
    if runtime.is_automation:
        raise RequestError(422, "任务会话由对应执行入口维护，仅只读")


def _require_str(params: dict[str, Any], key: str) -> str:
    v = params.get(key)
    if not isinstance(v, str):
        raise RequestError(422, f"{key} must be a string")
    return v


def _session_video_file_id(file_url: str, session_id: str) -> str | None:
    """视频附件只认本会话的后端上传 URL（相对路径或 public_base_url 前缀），返回其 file_id；任意第三方绝对 URL 会让供应商替我们发任意请求，必须绑死前缀。"""
    return video_file_id_from_url(file_url, session_id) if len(file_url) <= 2048 else None


def _validate_attachments(params: dict[str, Any], session_id: str) -> list[ChatAttachment] | None:
    """校验并规范化 attachments（每项重塑为 {type, file_url}），未传时返回 None。image 接受 HTTP(S) 与 data:image URL；音视频只认本会话后端上传且文件仍在的 URL（须先经对应媒体上传接口）。"""
    raw = params.get("attachments")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise RequestError(422, "attachments must be a list")
    if len(raw) > SETTINGS.max_attachments_per_turn:
        raise RequestError(422, f"too many attachments (max {SETTINGS.max_attachments_per_turn})")
    cleaned: list[ChatAttachment] = []
    for idx, att in enumerate(raw):
        if not isinstance(att, dict):
            raise RequestError(422, f"attachments[{idx}] must be an object")
        att_type = att.get("type")
        if att_type not in (ATTACHMENT_TYPE_IMAGE, ATTACHMENT_TYPE_VIDEO, ATTACHMENT_TYPE_AUDIO):
            raise RequestError(422, f"attachments[{idx}].type must be image, video or audio")

        file_url = att.get("file_url")
        if not (file_url and isinstance(file_url, str)):
            raise RequestError(422, f"attachments[{idx}] must have file_url")

        if att_type == ATTACHMENT_TYPE_AUDIO:
            file_id = audio_file_id_from_url(file_url, session_id)
            if file_id is None or resolve_audio_file(session_id, file_id) is None:
                raise RequestError(422, "音频附件不属于当前会话或已失效，请重新上传")
        elif att_type == ATTACHMENT_TYPE_VIDEO:
            if file_url.startswith("data:"):
                raise RequestError(
                    422,
                    f"attachments[{idx}] video must reference an uploaded URL, not a data URL",
                )
            file_id = _session_video_file_id(file_url, session_id)
            if file_id is None:
                raise RequestError(
                    422,
                    f"attachments[{idx}].file_url must be a video URL uploaded to this session (/api/media/videos/{session_id}/...)",
                )
            # 文件可能已被配额剔除或清理；拒绝提交，不把死链写入历史或交给供应商。
            if resolve_video_file(session_id, file_id) is None:
                raise RequestError(422, "视频附件已失效，请重新上传")
        elif file_url.startswith("http"):
            # HTTP/HTTPS URL（长度与 URL 语义一致，维持紧上限）
            if len(file_url) > 2048:
                raise RequestError(422, f"attachments[{idx}].file_url too long")
        elif file_url.startswith("data:image/"):
            # 桌面端本地图片 data URL：字节在负载内，不落盘；字符上限覆盖 base64 膨胀。
            if len(file_url) > ATTACHMENT_DATA_URL_MAX_CHARS:
                raise RequestError(422, f"attachments[{idx}].file_url too long")
        else:
            raise RequestError(422, f"attachments[{idx}] must have file_url")
        cleaned.append(ChatAttachment(type=att_type, file_url=file_url))
    return cleaned
