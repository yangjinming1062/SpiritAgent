"""形象制作入口的门禁与交付 DTO，模型及资源处理仍由制作服务维护。"""

from infrastructure.database import SESSION_LOCAL

from features.appearance.avatar_service import raise_if_image_sealed, regenerate_avatar
from features.appearance.response_builders import avatar_response
from features.appearance.schemas import AvatarAssetResponse, ImageReviseMode
from features.companion.visual_state import ensure_persona_snapshot


class AvatarOnboardingRequiredError(ValueError):
    pass


async def check_avatar_regeneration(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        persona = await ensure_persona_snapshot(db, user_id)
        if not persona.is_complete:
            raise AvatarOnboardingRequiredError("finish onboarding before regenerating avatar")
        await raise_if_image_sealed(db, user_id, persona)


async def regenerate_avatar_view(user_id: int, feedback: str | None, mode: ImageReviseMode) -> AvatarAssetResponse:
    return avatar_response(await regenerate_avatar(user_id=user_id, feedback=feedback, mode=mode))
