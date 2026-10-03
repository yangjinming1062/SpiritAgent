from components import safe_json_loads
from modules.companion import (
    AvatarAsset,
    AvatarAssetResponse,
    CompanionOutfit,
    OutfitResponse,
)

from .avatar_service import re_sign_bare_path


def avatar_response(asset: AvatarAsset) -> AvatarAssetResponse:
    """仅在响应出口签名，存储对象始终保留裸路径。"""
    prompt_payload = safe_json_loads(asset.prompt_json, default={})
    payload = prompt_payload if isinstance(prompt_payload, dict) else {}
    return AvatarAssetResponse(
        id=asset.id,
        asset_url=re_sign_bare_path(asset.asset_url) or asset.asset_url,
        seed_fullbody_url=re_sign_bare_path(asset.seed_fullbody_url) or asset.seed_fullbody_url or "",
        is_fullbody_confirmed=asset.is_fullbody_confirmed,
        prompt=payload.get("avatar_prompt", ""),
        status="succeeded",
    )


def outfit_response(outfit: CompanionOutfit) -> OutfitResponse:
    """外观行转接口响应；立绘路径重签名（temp-media 草稿转 /api/media/files 形式）。"""
    return OutfitResponse.model_validate(outfit, from_attributes=True).model_copy(
        update={"fullbody_url": re_sign_bare_path(outfit.fullbody_url) or ""},
    )
