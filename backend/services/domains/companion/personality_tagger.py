import json
from typing import Protocol

from components import get_logger, safe_json_loads
from prompts.companion import PERSONALITY_TAGGER_PROMPT
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.llm import ProviderConfig

logger = get_logger(__name__)


class ChatFn(Protocol):
    async def __call__(
        self,
        db: AsyncSession | None,
        user_id: int | None,
        system_prompt: str,
        user_payload: str,
        *,
        provider_config: ProviderConfig | None = None,
    ) -> str: ...


async def analyze_personality_tags(
    chat: ChatFn,
    definition_json: str,
    user_id: int | None = None,
    *,
    species: str | None = None,
    db: AsyncSession | None = None,
    provider_config: ProviderConfig | None = None,
) -> list[str]:
    """LLM 分析 persona 设定，返回最多 10 个去重后的性格标签；无依据时可为空。"""
    try:
        raw_data = safe_json_loads(definition_json, default={})
        data = raw_data if isinstance(raw_data, dict) else {}

        user_payload = json.dumps(
            {
                "name": data.get("name", "角色"),
                "species": species or data.get("biological_type") or "",
                "personality": data.get("personality") or "",
                "speaking_style": data.get("speaking_style") or "",
            },
            ensure_ascii=False,
        )

        raw = await chat(db, user_id, PERSONALITY_TAGGER_PROMPT, user_payload, provider_config=provider_config)
        cleaned_raw = raw.strip()
        if cleaned_raw.startswith("```"):
            cleaned_raw = cleaned_raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = safe_json_loads(cleaned_raw, default=None)
        if isinstance(parsed, list):
            tags = [t.strip() for t in parsed if isinstance(t, str) and t.strip()]
        else:
            return []

        # 去重且保持顺序
        if deduped := list(dict.fromkeys(tags)):
            return deduped[:10]

        return []
    except Exception:
        logger.warning("Failed to analyze personality tags with LLM", exc_info=True)
        return []
