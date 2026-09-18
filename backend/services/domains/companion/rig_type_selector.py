import json
import re
from typing import Protocol

from components import safe_json_loads
from prompts.companion import RIG_TYPE_SELECTOR_PROMPT
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.llm import ProviderConfig

_RIG_TYPES: tuple[str, ...] = ("biped", "quadruped", "avian", "serpentine", "aquatic", "hexapod", "octopod")

# 自定义伙伴以人形为主、二次元是产品的主要风格载体，故分类失败时退到主流路径而非小众路径
_DEFAULT_CLASSIFICATION: tuple[str, bool] = ("biped", True)


class _ChatFn(Protocol):
    async def __call__(
        self,
        db: AsyncSession | None,
        user_id: int | None,
        system_prompt: str,
        user_payload: str,
        *,
        provider_config: ProviderConfig | None = None,
    ) -> str: ...


async def classify_species(
    chat: _ChatFn,
    species: str,
    *,
    db: AsyncSession | None = None,
    user_id: int | None = None,
) -> tuple[str, bool]:
    """由 LLM 判定骨骼类型与是否类人面孔；任何异常都回落默认值而不抛错——判错只影响风格与动画库选择，不影响正确性。"""
    try:
        species_text = species.strip() or "人类"
        user_payload = json.dumps({"species": species_text}, ensure_ascii=False)
        raw = await chat(db, user_id, RIG_TYPE_SELECTOR_PROMPT, user_payload)
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        data = safe_json_loads(match.group(0), default=None) if match else None
        if not isinstance(data, dict):
            return _DEFAULT_CLASSIFICATION
        candidate = str(data.get("rig_type", "")).strip().lower()
        face = data.get("has_humanoid_face")
        rig = candidate if candidate in _RIG_TYPES else "biped"
        return rig, (face if isinstance(face, bool) else True)
    except Exception:
        return _DEFAULT_CLASSIFICATION


async def select_rig_type(
    chat: _ChatFn,
    species: str,
    *,
    db: AsyncSession | None = None,
    user_id: int | None = None,
) -> str:
    """classify_species 的骨骼类型视图，供不关心类人面孔标志的调用方使用。"""
    return (await classify_species(chat, species, db=db, user_id=user_id))[0]
