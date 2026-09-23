"""资产基础设施：文件存储、签名 URL、散列与 Range 文件服务。"""

from . import asset_store
from .asset_store import (
    build_data_uri,
    build_signed_asset_url,
    build_signed_avatar_url,
    client_asset_url,
    compute_file_sha256,
    parse_companion_asset_path,
    resolve_companion_asset_path,
    save_companion_asset,
    save_companion_asset_async,
    save_video_job_asset_async,
    signed_companion_asset_url,
    sniff_media_ext,
    unlink_companion_asset,
    verify_signed_asset_request,
    verify_signed_avatar_request,
    video_job_asset_path,
)
from .http_range import serve_ranged_file

__all__ = [
    "asset_store",
    "build_data_uri",
    "build_signed_asset_url",
    "build_signed_avatar_url",
    "client_asset_url",
    "compute_file_sha256",
    "parse_companion_asset_path",
    "resolve_companion_asset_path",
    "save_companion_asset",
    "save_companion_asset_async",
    "save_video_job_asset_async",
    "serve_ranged_file",
    "signed_companion_asset_url",
    "video_job_asset_path",
    "sniff_media_ext",
    "unlink_companion_asset",
    "verify_signed_asset_request",
    "verify_signed_avatar_request",
]
