"""媒体业务域：聊天视频附件的记录、配额与引用规则。"""

from services.domains.media.chat_videos import (
    attachment_video_url,
    enforce_session_quota,
    inline_video_parts,
    prune_videos_in_range,
    resolve_video_file,
    save_video_attachment,
    video_mime_for_ext,
)

__all__ = [
    "attachment_video_url",
    "enforce_session_quota",
    "inline_video_parts",
    "prune_videos_in_range",
    "resolve_video_file",
    "save_video_attachment",
    "video_mime_for_ext",
]
