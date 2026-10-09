from components import DEFAULT_LANGUAGE, resolve_prompt_text
from modules.companion import CompanionOutfit
from prompts.companion import OUTFIT_DESCRIPTION_LABELS_TEXTS, OUTFIT_LABELS_TEXTS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def build_outfit_extras(
    db: AsyncSession,
    user_id: int,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """返回当前着装描述；描述未就绪时为空，避免重复注入基础形象已经表达的信息。"""
    outfit = (
        await db.execute(
            select(CompanionOutfit).where(
                CompanionOutfit.user_id == user_id,
                CompanionOutfit.active.is_(True),
                CompanionOutfit.status == "ready",
                CompanionOutfit.description_status == "ready",
            ),
        )
    ).scalar_one_or_none()
    if outfit is None or not (outfit.description or "").strip():
        return ""
    label = resolve_prompt_text(OUTFIT_LABELS_TEXTS, language)
    description_label = resolve_prompt_text(OUTFIT_DESCRIPTION_LABELS_TEXTS, language)
    description = outfit.description.strip()[:500]
    return f"{label}\n{description_label}: {description}"
