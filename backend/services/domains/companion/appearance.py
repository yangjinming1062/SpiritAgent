from components import DEFAULT_LANGUAGE, resolve_prompt_text
from modules.companion import Companion3DModel, CompanionOutfit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 双语着装块标题。
_OUTFIT_LABELS_TEXTS: dict[str, str] = {
    "zh": "# 当前着装",
    "en": "# Current outfit",
}

# 双语着装块 fallback：用户尚未穿任何 outfit 时使用，确保 caller 拿到的 outfit_block 非空。
_DEFAULT_OUTFIT_TEXTS: dict[str, str] = {
    "zh": "当前着装：（默认形象，尚未换装）",
    "en": "Current outfit: (default appearance, no outfit set)",
}


async def build_outfit_extras(
    db: AsyncSession,
    user_id: int,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """当前穿着的着装描述，注入系统提示词稳定段——伙伴自知穿着，为着装联动打底。

    无 outfit 时返回双语 fallback，caller 不必再包一层默认文案。
    """
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
        return resolve_prompt_text(_DEFAULT_OUTFIT_TEXTS, language)
    label = resolve_prompt_text(_OUTFIT_LABELS_TEXTS, language)
    return f"{label}\n{outfit.name}:{outfit.description.strip()[:600]}"


async def get_active_model(db: AsyncSession, user_id: int) -> Companion3DModel | None:
    return (
        await db.execute(
            select(Companion3DModel).where(Companion3DModel.user_id == user_id, Companion3DModel.active.is_(True)),
        )
    ).scalar_one_or_none()
