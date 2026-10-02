import json

from components import parse_llm_json
from prompts.companion import PERSONALITY_TAGGER_PROMPT

from services.infrastructure.llm import ProviderConfig, chat


def personality_tag_inputs(definition: dict[str, str]) -> dict[str, str]:
    """标签依据的人设字段；请求与写入前的依据比对共用，其余字段变化不使标签失效。"""
    return {
        "name": definition.get("name", "角色"),
        "species": definition.get("biological_type") or "",
        "personality": definition.get("personality") or "",
        "speaking_style": definition.get("speaking_style") or "",
    }


async def analyze_personality_tags(definition: dict[str, str], provider_config: ProviderConfig) -> list[str]:
    """LLM 分析人设，返回最多 10 个去重后的性格标签；无依据时可为空。调用失败或输出不是数组时抛出，由调用方重试。"""
    user_payload = json.dumps(personality_tag_inputs(definition), ensure_ascii=False)
    raw = await chat(None, PERSONALITY_TAGGER_PROMPT, user_payload, provider_config=provider_config)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, list):
        raise ValueError("Personality tagger response is not a JSON array")
    tags = [tag.strip() for tag in parsed if isinstance(tag, str) and tag.strip()]
    return list(dict.fromkeys(tags))[:10]
