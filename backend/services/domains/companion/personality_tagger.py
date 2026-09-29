import json

from components import parse_llm_json
from prompts.companion import PERSONALITY_TAGGER_PROMPT

from services.infrastructure.llm import ProviderConfig, chat


async def analyze_personality_tags(definition: dict[str, str], provider_config: ProviderConfig) -> list[str]:
    """LLM 分析人设，返回最多 10 个去重后的性格标签；无依据时可为空。调用失败或输出不是数组时抛出，由调用方重试。"""
    user_payload = json.dumps(
        {
            "name": definition.get("name", "角色"),
            "species": definition.get("biological_type") or "",
            "personality": definition.get("personality") or "",
            "speaking_style": definition.get("speaking_style") or "",
        },
        ensure_ascii=False,
    )
    raw = await chat(None, None, PERSONALITY_TAGGER_PROMPT, user_payload, provider_config=provider_config)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, list):
        raise ValueError("Personality tagger response is not a JSON array")
    tags = [tag.strip() for tag in parsed if isinstance(tag, str) and tag.strip()]
    return list(dict.fromkeys(tags))[:10]
