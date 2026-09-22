"""角色动作素材编排：片段处理、目录发布、按参考生成、激活与恢复。"""

from .manifest import (
    VideoClipSpec,
    VideoPackCanvas,
)
from .service import (
    VideoPackError,
    VideoPackNotFoundError,
    VideoPackStateError,
    activate_pack,
    create_pack_from_clips,
    create_pack_from_reference,
    delete_pack,
    drain_video_generation,
    list_pack_responses,
    pack_response,
    resume_processing_packs,
    resume_video_generation_jobs,
    retry_pack,
)

__all__ = [
    "VideoClipSpec",
    "VideoPackCanvas",
    "VideoPackError",
    "VideoPackNotFoundError",
    "VideoPackStateError",
    "activate_pack",
    "create_pack_from_clips",
    "create_pack_from_reference",
    "delete_pack",
    "drain_video_generation",
    "list_pack_responses",
    "pack_response",
    "retry_pack",
    "resume_processing_packs",
    "resume_video_generation_jobs",
]
