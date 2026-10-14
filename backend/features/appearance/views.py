"""头像与衣柜公开操作：返回交付 DTO，持久对象由制作服务所有。"""

from sqlalchemy.ext.asyncio import AsyncSession

from features.appearance import avatar_service as _avatar
from features.appearance import outfit_service as _outfit
from features.appearance.character_card import character_card_response, get_character_card
from features.appearance.character_card_models import CharacterCardResponse
from features.appearance.response_builders import avatar_response, outfit_response
from features.appearance.schemas import AvatarAssetResponse, FullbodyCandidateResponse, ImageReviseMode, OutfitResponse


async def accept_fullbody_candidate_view(user_id: int, avatar_id: int, candidate_id: int) -> AvatarAssetResponse:
    return avatar_response(
        await _avatar.accept_fullbody_candidate(user_id=user_id, avatar_id=avatar_id, candidate_id=candidate_id),
    )


async def generate_avatar_view(user_id: int, *, feedback: str | None = None) -> AvatarAssetResponse:
    return avatar_response(await _avatar.generate_avatar(user_id=user_id, feedback=feedback))


async def get_active_avatar_view(db: AsyncSession, user_id: int) -> AvatarAssetResponse | None:
    asset = await _avatar.get_active_avatar(db=db, user_id=user_id)
    return avatar_response(asset) if asset is not None else None


async def select_avatar_view(db: AsyncSession, user_id: int, avatar_id: int) -> AvatarAssetResponse:
    return avatar_response(await _avatar.select_avatar(db=db, user_id=user_id, avatar_id=avatar_id))


async def list_avatar_history_view(db: AsyncSession, user_id: int, limit: int = 20) -> list[AvatarAssetResponse]:
    return [avatar_response(asset) for asset in await _avatar.list_avatar_history(db=db, user_id=user_id, limit=limit)]


async def regenerate_avatar_from_image_view(
    user_id: int,
    *,
    data: bytes = b"",
    content_type: str = "image/png",
    description: str | None = None,
    presentation_data: bytes | None = None,
    presentation_content_type: str | None = None,
) -> AvatarAssetResponse:
    return avatar_response(
        await _avatar.regenerate_avatar_from_image(
            user_id=user_id,
            data=data,
            content_type=content_type,
            description=description,
            presentation_data=presentation_data,
            presentation_content_type=presentation_content_type,
        ),
    )


async def finalize_avatar_view(db: AsyncSession, user_id: int) -> AvatarAssetResponse | None:
    asset = await _avatar.finalize_avatar(db=db, user_id=user_id)
    return avatar_response(asset) if asset is not None else None


async def generate_fullbody_reference_view(
    user_id: int,
    *,
    avatar_id: int,
    mode: ImageReviseMode,
    feedback: str | None = None,
    reference_image: bytes | None = None,
    reference_content_type: str | None = None,
    candidate_id: int | None = None,
) -> AvatarAssetResponse | FullbodyCandidateResponse:
    asset = await _avatar.generate_fullbody_reference(
        user_id=user_id,
        avatar_id=avatar_id,
        mode=mode,
        feedback=feedback,
        reference_image=reference_image,
        reference_content_type=reference_content_type,
        candidate_id=candidate_id,
    )
    return asset if isinstance(asset, FullbodyCandidateResponse) else avatar_response(asset)


async def confirm_fullbody_seed_view(user_id: int, *, avatar_id: int, expected_url: str) -> AvatarAssetResponse:
    return avatar_response(
        await _avatar.confirm_fullbody_seed(user_id=user_id, avatar_id=avatar_id, expected_url=expected_url),
    )


async def adopt_fullbody_seed_view(
    user_id: int,
    *,
    avatar_id: int,
    data: bytes,
    content_type: str,
) -> AvatarAssetResponse | FullbodyCandidateResponse:
    asset = await _avatar.adopt_fullbody_seed(
        user_id=user_id,
        avatar_id=avatar_id,
        data=data,
        content_type=content_type,
    )
    return asset if isinstance(asset, FullbodyCandidateResponse) else avatar_response(asset)


async def adopt_avatar_seed_view(user_id: int, *, data: bytes, content_type: str) -> AvatarAssetResponse:
    return avatar_response(await _avatar.adopt_avatar_seed(user_id=user_id, data=data, content_type=content_type))


async def create_outfit_draft_view(
    db: AsyncSession,
    user_id: int,
    *,
    description: str | None,
    image: bytes | None = None,
    content_type: str | None = None,
) -> OutfitResponse:
    return outfit_response(
        await _outfit.create_outfit_draft(
            db=db,
            user_id=user_id,
            description=description,
            image=image,
            content_type=content_type,
        ),
    )


async def regenerate_outfit_draft_view(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    *,
    feedback: str | None,
    mode: ImageReviseMode,
) -> OutfitResponse:
    return outfit_response(
        await _outfit.regenerate_outfit_draft(
            db=db,
            user_id=user_id,
            outfit_id=outfit_id,
            feedback=feedback,
            mode=mode,
        ),
    )


async def confirm_outfit_view(db: AsyncSession, user_id: int, outfit_id: int) -> OutfitResponse:
    return outfit_response(await _outfit.confirm_outfit(db=db, user_id=user_id, outfit_id=outfit_id))


async def adopt_outfit_draft_image_view(
    db: AsyncSession,
    user_id: int,
    *,
    description: str | None,
    data: bytes,
    content_type: str | None,
) -> OutfitResponse:
    return outfit_response(
        await _outfit.adopt_outfit_draft_image(
            db=db,
            user_id=user_id,
            description=description,
            data=data,
            content_type=content_type,
        ),
    )


async def adopt_outfit_regenerate_image_view(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    *,
    data: bytes,
    content_type: str | None,
) -> OutfitResponse:
    return outfit_response(
        await _outfit.adopt_outfit_regenerate_image(
            db=db,
            user_id=user_id,
            outfit_id=outfit_id,
            data=data,
            content_type=content_type,
        ),
    )


async def retry_outfit_description_view(db: AsyncSession, user_id: int, outfit_id: int) -> OutfitResponse:
    return outfit_response(await _outfit.retry_outfit_description(db=db, user_id=user_id, outfit_id=outfit_id))


async def character_card_view(db: AsyncSession, user_id: int) -> CharacterCardResponse | None:
    row = await get_character_card(db, user_id)
    return character_card_response(row) if row is not None else None
