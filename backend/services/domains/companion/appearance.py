from components import DEFAULT_LANGUAGE, resolve_prompt_text
from modules.companion import Companion3DModel, CompanionOutfit
from prompts.companion import OUTFIT_LABELS_TEXTS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def build_outfit_extras(
    db: AsyncSession,
    user_id: int,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """返回当前换装描述；未换装时为空，避免重复注入基础形象已经表达的信息。"""
    outfit = (
        await db.execute(
            select(CompanionOutfit).where(
                CompanionOutfit.user_id == user_id,
                CompanionOutfit.active.is_(True),
                CompanionOutfit.status == "ready",
            ),
        )
    ).scalar_one_or_none()
    if outfit is None or not (outfit.description or "").strip():
        return ""
    label = resolve_prompt_text(OUTFIT_LABELS_TEXTS, language)
    return f"{label}\n{outfit.name}:{outfit.description.strip()[:600]}"


async def get_active_model(db: AsyncSession, user_id: int) -> Companion3DModel | None:
    return (
        await db.execute(
            select(Companion3DModel).where(Companion3DModel.user_id == user_id, Companion3DModel.active.is_(True)),
        )
    ).scalar_one_or_none()
