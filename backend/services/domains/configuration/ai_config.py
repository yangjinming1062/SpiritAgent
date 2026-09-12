import copy
from typing import Any

from components import CAPABILITY_SERVICES, AIConfig, AIConfigPublic, AIConfigUpdate
from fastapi import HTTPException
from pydantic import ValidationError

from services.infrastructure.llm import providers_supporting


def prepare_ai_config(raw: Any, previous: AIConfig | None = None) -> AIConfig:
    value = raw.model_dump() if isinstance(raw, AIConfigUpdate) else copy.deepcopy(raw)
    if not isinstance(value, dict) or "providers" not in value or "capabilities" not in value:
        raise HTTPException(422, "AI 配置必须同时包含供应商库与能力配置")
    old_value = (previous or AIConfig()).model_dump()
    try:
        groups = [(value.get("providers", []), old_value["providers"])]
        groups.extend(
            (cards, old_value["capabilities"].get(service, []))
            for service, cards in value.get("capabilities", {}).items()
        )
        for cards, old_cards in groups:
            old = {card["provider"]: card for card in old_cards}
            for card in cards:
                card.pop("api_key_set", None)
                clear = card.pop("clear_api_key", False) is True
                if isinstance(card.get("api_key"), str):
                    card["api_key"] = card["api_key"].strip()
                prior = old.get(card.get("provider"), {})
                if clear:
                    card["api_key"] = ""
                elif not card.get("api_key"):
                    card["api_key"] = prior.get("api_key", "")
        config = AIConfig.model_validate(value)

        for service in CAPABILITY_SERVICES:
            supported = set(providers_supporting(service))
            if any(card.provider not in supported for card in getattr(config.capabilities, service)):
                raise HTTPException(422, f"所选供应商不支持 {service} 能力")
        return config
    except (ValidationError, AttributeError, TypeError, ValueError):
        raise HTTPException(422, "AI 卡片配置无效，请检查字段与重复的供应商") from None


def public_ai_config(config: AIConfig) -> AIConfigPublic:
    value = config.model_dump()
    for cards in [value["providers"], *value["capabilities"].values()]:
        for card in cards:
            card["api_key_set"] = bool(card["api_key"])
            card["api_key"] = ""
    return AIConfigPublic.model_validate(value)
