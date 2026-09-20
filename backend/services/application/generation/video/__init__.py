"""角色视频动作包编排（第三步视频链）：manifest、构建与发布、激活与恢复。"""

from .manifest import (
    MANIFEST_SCHEMA,
    REQUIRED_ACTIONS,
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
    delete_pack,
    list_pack_responses,
    pack_response,
    resume_processing_packs,
)

__all__ = [
    "MANIFEST_SCHEMA",
    "REQUIRED_ACTIONS",
    "ManifestValidationError",
    "VideoClipSpec",
    "VideoPackCanvas",
    "VideoPackError",
    "VideoPackManifest",
    "VideoPackNotFoundError",
    "VideoPackStateError",
    "activate_pack",
    "create_pack_from_clips",
    "delete_pack",
    "list_pack_responses",
    "pack_response",
    "resume_processing_packs",
    "validate_pack_manifest",
]
