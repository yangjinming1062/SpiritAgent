import contextlib

from components import safe_json_loads
from modules.companion import (
    AvatarAsset,
    AvatarAssetResponse,
    Companion3DModel,
    Companion3DModelResponse,
    CompanionOutfit,
    OutfitResponse,
)

from services.image_to_3d import provider_supports_multiview

from .asset_store import get_companion_model_sha256
from .avatar_service import re_sign_bare_path
from .pipeline import signed_model_url


def avatar_response(asset: AvatarAsset) -> AvatarAssetResponse:
    """把头像行转换为接口响应。"""
    prompt_payload = safe_json_loads(asset.prompt_json, default={})
    payload = prompt_payload if isinstance(prompt_payload, dict) else {}
    return AvatarAssetResponse(
        id=asset.id,
        asset_url=asset.asset_url,
        seed_front_2d_url=getattr(asset, "seed_front_2d_url", None) or "",
        seed_front_3d_url=getattr(asset, "seed_front_3d_url", None) or "",
        seed_back_url=getattr(asset, "seed_back_url", None) or "",
        supports_multiview=provider_supports_multiview(),
        fullbody_style=str(payload.get("fullbody_style") or ""),
        prompt=payload.get("prompt", ""),
        status="succeeded",
    )


def outfit_response(outfit: CompanionOutfit) -> OutfitResponse:
    """外观行转接口响应；立绘路径重签名（temp-media 草稿转 /api/media/files 形式）。"""
    return OutfitResponse(
        id=outfit.id,
        name=outfit.name,
        description=outfit.description,
        fullbody_url=re_sign_bare_path(outfit.fullbody_url) or "",
        style=outfit.style or "cel_shading",
        status=outfit.status,
        active=outfit.active,
        pending_wear=outfit.pending_wear,
    )


def model_response(model: Companion3DModel) -> Companion3DModelResponse:
    """把 3D 模型行转换为接口响应，补齐签名地址与内容哈希。"""
    content_hash = model.content_hash or None
    if not content_hash and model.asset_url:
        parts = model.asset_url.split("/", 2)
        if len(parts) == 3:
            with contextlib.suppress(Exception):
                content_hash = get_companion_model_sha256(int(parts[1]), parts[2])

    return Companion3DModelResponse(
        id=model.id,
        species=model.species,
        provider=model.provider,
        asset_url=signed_model_url(model) or model.asset_url,
        status=model.status,
        has_rig=model.has_rig,
        rig_type=model.rig_type,
        rig_naming=model.rig_naming,
        style=model.style or "realistic",
        content_hash=content_hash,
        clip_map=safe_json_loads(model.clip_map_json or "{}", default={}),
    )
