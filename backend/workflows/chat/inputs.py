import contextlib
from collections.abc import AsyncIterator

from features.conversation.attachments import (
    audio_file_id_from_url,
    resolve_audio_file,
    resolve_video_file,
    video_file_id_from_url,
)
from features.conversation.request_contracts import ChatAttachment
from features.conversation.session_operations import ConversationNotFoundError, get_conversation
from features.conversation.snapshots import ConversationSnapshot
from infrastructure.config import SETTINGS
from infrastructure.constants import (
    ATTACHMENT_DATA_URL_MAX_CHARS,
    ATTACHMENT_TYPE_AUDIO,
    ATTACHMENT_TYPE_VIDEO,
)
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


def _reject_read_only_session(runtime: RuntimeSession) -> None:
    """自动化历史只由任务执行入口写入。"""
    if runtime.is_automation:
        raise RequestError(422, "任务会话由对应执行入口维护，仅只读")


def _session_video_file_id(file_url: str, session_id: str) -> str | None:
    """视频附件只认本会话的后端上传 URL（相对路径或 public_base_url 前缀），返回其 file_id；任意第三方绝对 URL 会让供应商替我们发任意请求，必须绑死前缀。"""
    return video_file_id_from_url(file_url, session_id) if len(file_url) <= 2048 else None


def _validate_attachments(attachments: list[ChatAttachment] | None, session_id: str) -> list[ChatAttachment] | None:
    """校验已解析附件的配额与媒体引用；音视频只认本会话上传且仍存在的文件。"""
    if attachments is None:
        return None
    if len(attachments) > SETTINGS.max_attachments_per_turn:
        raise RequestError(422, f"too many attachments (max {SETTINGS.max_attachments_per_turn})")
    for idx, att in enumerate(attachments):
        att_type = att.type
        file_url = att.file_url

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
    return attachments
