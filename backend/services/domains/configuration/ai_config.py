from components import CAPABILITY_SERVICES, AIConfig, AIConfigPublic, AIConfigUpdate, ProviderCard, ProviderCardUpdate
from pydantic import ValidationError

from services.infrastructure.llm import providers_supporting


def _merge_saved_keys(cards: list[ProviderCardUpdate], saved: list[ProviderCard]) -> list[dict[str, str]]:
    """留空的 API Key 沿用同一供应商已保存的值，clear_api_key 显式清空。"""
    saved_keys = {card.provider: card.api_key for card in saved}
    return [
        {
            **card.model_dump(exclude={"api_key_set", "clear_api_key"}),
            "api_key": "" if card.clear_api_key else card.api_key or saved_keys.get(card.provider, ""),
        }
        for card in cards
    ]


def prepare_ai_config(update: AIConfigUpdate, previous: AIConfig | None) -> AIConfig:
    """合并已保存密钥后整批校验；失败抛 ValueError（含具体原因），由调用方决定错误呈现。"""
    saved = previous or AIConfig()
    try:
        config = AIConfig.model_validate(
            {
                "providers": _merge_saved_keys(update.providers, saved.providers),
                "capabilities": {
                    service: _merge_saved_keys(
                        getattr(update.capabilities, service),
                        getattr(saved.capabilities, service),
                    )
                    for service in CAPABILITY_SERVICES
                },
            },
        )
    except ValidationError as exc:
        raise ValueError("AI 卡片配置无效，请检查字段与重复的供应商") from exc

    for service in CAPABILITY_SERVICES:
        supported = set(providers_supporting(service))
        if any(card.provider not in supported for card in getattr(config.capabilities, service)):
            raise ValueError(f"所选供应商不支持 {service} 能力")
    return config


def public_ai_config(config: AIConfig) -> AIConfigPublic:
    value = config.model_dump()
    for cards in [value["providers"], *value["capabilities"].values()]:
        for card in cards:
            card["api_key_set"] = bool(card["api_key"])
            card["api_key"] = ""
    return AIConfigPublic.model_validate(value)
