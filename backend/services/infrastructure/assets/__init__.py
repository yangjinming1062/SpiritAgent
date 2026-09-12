"""资产基础设施：文件存储、签名 URL、散列与 Range 文件服务。"""

from services.infrastructure.assets import asset_store
from services.infrastructure.assets.asset_store import (
    build_data_uri,
    build_signed_asset_url,
    build_signed_avatar_url,
    build_signed_model_url,
    client_asset_url,
    compress_glb,
    compute_bytes_sha256,
    compute_file_sha256,
    decompress_glb_if_needed,
    get_companion_model_sha256,
    parse_companion_asset_path,
    resolve_companion_asset_path,
    resolve_companion_model_path,
    save_companion_asset,
    verify_signed_asset_request,
    verify_signed_avatar_request,
)
from services.infrastructure.assets.http_range import serve_ranged_file

__all__ = [
    "asset_store",
    "build_data_uri",
    "build_signed_asset_url",
    "build_signed_avatar_url",
    "build_signed_model_url",
    "client_asset_url",
    "compress_glb",
    "compute_bytes_sha256",
    "compute_file_sha256",
    "decompress_glb_if_needed",
    "get_companion_model_sha256",
    "parse_companion_asset_path",
    "resolve_companion_asset_path",
    "resolve_companion_model_path",
    "save_companion_asset",
    "serve_ranged_file",
    "verify_signed_asset_request",
    "verify_signed_avatar_request",
]
