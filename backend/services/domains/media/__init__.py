"""媒体业务域：聊天音视频附件的记录、配额与引用规则。"""

from .audio_input import ensure_audio_transcripts, prepare_audio_input
from .chat_attachments import (
    attachment_audio_url,
    attachment_video_url,
    audio_file_id_from_url,
    audio_mime_for_ext,
    copy_forked_media_attachments,
    enforce_session_quota,
    inline_video_parts,
    prune_media_in_range,
    resolve_audio_file,
    resolve_video_file,
    save_audio_attachment,
    save_video_attachment,
    video_file_id_from_url,
    video_mime_for_ext,
)

__all__ = [
    "ensure_audio_transcripts",
    "prepare_audio_input",
    "attachment_audio_url",
    "audio_file_id_from_url",
    "audio_mime_for_ext",
    "resolve_audio_file",
    "save_audio_attachment",
    "attachment_video_url",
    "copy_forked_media_attachments",
    "enforce_session_quota",
    "inline_video_parts",
    "prune_media_in_range",
    "resolve_video_file",
    "save_video_attachment",
    "video_file_id_from_url",
    "video_mime_for_ext",
]
