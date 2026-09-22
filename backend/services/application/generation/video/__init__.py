"""角色视频动作包编排（第三步视频链）：manifest、构建与发布、按参考生成、激活与恢复。"""

from .manifest import (
    MANIFEST_SCHEMA,
    ManifestValidationError,
    VideoClipSpec,
    VideoPackCanvas,
    VideoPackManifest,
    validate_pack_manifest,
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
    "MANIFEST_SCHEMA",
    "ManifestValidationError",
    "VideoClipSpec",
    "VideoPackCanvas",
    "VideoPackError",
    "VideoPackManifest",
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
    "validate_pack_manifest",
]
