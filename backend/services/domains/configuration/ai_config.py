from components import (
    AI_FEATURE_BY_ID,
    CAPABILITY_SERVICES,
    AIConfig,
    AIConfigPublic,
    AIConfigUpdate,
    ProviderCard,
    ProviderCardUpdate,
)
from pydantic import ValidationError

from services.infrastructure.llm import model_supports_feature, providers_supporting


def validate_ai_config(config: AIConfig, system: AIConfig | None = None) -> None:
    """校验当前作用域的引用与适配器能力；系统卡片始终从系统信息库解析。"""
    config.validate_references(system)
    registered = {name for service in CAPABILITY_SERVICES for name in providers_supporting(service)}
    for provider in config.providers:
        if provider.provider_type not in registered:
            raise ValueError(f"供应商 {provider.name} 的类型未注册：{provider.provider_type}")
    for service, card in config.models():
        provider = config.find_provider(card.provider_id, system)
        if provider is None or provider.provider_type not in providers_supporting(service):
            raise ValueError(f"模型 {card.model_name} 的供应商不支持 {service} 能力")
    for feature, value in config.features.items():
        for model_id in value.chain:
            model = config.find_model(model_id, system)
            if model is None:
                raise ValueError(f"功能 {feature.value} 引用的模型卡片不存在：{model_id}")
            _, card, owner = model
            provider = owner.find_provider(card.provider_id, system if owner is config else None)
            if provider is None or not model_supports_feature(
                provider.provider_type,
                card.model_name,
                feature,
                input_modalities=card.input_modalities,
                output_modalities=card.output_modalities,
            ):
                raise ValueError(f"模型 {card.model_name} 不支持功能 {AI_FEATURE_BY_ID[feature].label}")


def prepare_ai_config(
    update: AIConfigUpdate,
    previous: AIConfig | None,
    system: AIConfig | None = None,
) -> AIConfig:
    """按稳定 ID 保留密钥，整批校验后返回候选配置；错误不回显配置值。"""
    saved = {card.id: card for card in previous.providers} if previous is not None else {}
    providers = []
    for card in update.providers:
        old = saved.get(card.id)
        providers.append(prepare_provider_card(card, old).model_dump(mode="json"))
    try:
        config = AIConfig.model_validate(
            {
                **update.model_dump(mode="json", exclude={"providers"}),
                "providers": providers,
            },
        )
    except ValidationError as exc:
        reasons = "；".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())
        raise ValueError(f"AI 卡片配置无效：{reasons}") from exc
    validate_ai_config(config, system)
    return config


def prepare_provider_card(update: ProviderCardUpdate, previous: ProviderCard | None) -> ProviderCard:
    if previous is not None and previous.provider_type != update.provider_type:
        raise ValueError("供应商类型不能修改，请创建新的供应商条目")
    return ProviderCard.model_validate(
        {
            **update.model_dump(exclude={"api_key_set", "clear_api_key"}),
            "api_key": "" if update.clear_api_key else update.api_key or (previous.api_key if previous else ""),
        },
    )


def public_ai_config(config: AIConfig) -> AIConfigPublic:
    value = config.model_dump(mode="json")
    for card in value["providers"]:
        card["api_key_set"] = bool(card["api_key"])
        card["api_key"] = ""
    return AIConfigPublic.model_validate(value)
