from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai_features import AI_FEATURE_BY_ID, AiFeature

CAPABILITY_SERVICES = ("llm", "stt", "tts", "image_gen", "video_gen", "embedding")


class ProviderCard(BaseModel):
    """供应商信息库卡片：只存密钥与地址，模型名属调用卡片。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    api_key: str = Field(default="", max_length=2048)
    base_url: str = Field(default="", max_length=2048)


class CapabilityCard(ProviderCard):
    model_name: str = Field(default="", max_length=128)
    context_window: int | None = Field(default=None, gt=0, le=9_007_199_254_740_991, strict=True)


class CapabilityChains(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: list[CapabilityCard] = Field(default_factory=list)
    stt: list[CapabilityCard] = Field(default_factory=list)
    tts: list[CapabilityCard] = Field(default_factory=list)
    image_gen: list[CapabilityCard] = Field(default_factory=list)
    video_gen: list[CapabilityCard] = Field(default_factory=list)
    embedding: list[CapabilityCard] = Field(default_factory=list)


class FeatureChain[Card: CapabilityCard](BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: list[Card] = Field(default_factory=list)
    attempt_timeout_seconds: float | None = Field(default=None, gt=0, le=86400, allow_inf_nan=False)
    total_timeout_seconds: float | None = Field(default=None, gt=0, le=86400, allow_inf_nan=False)


class AIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCard] = Field(default_factory=list)
    capabilities: CapabilityChains = Field(default_factory=CapabilityChains)
    features: dict[AiFeature, FeatureChain[CapabilityCard]] = Field(default_factory=dict)

    def chains(self) -> list[tuple[str, list[CapabilityCard]]]:
        return [
            *((service, getattr(self.capabilities, service)) for service in CAPABILITY_SERVICES),
            *((AI_FEATURE_BY_ID[feature].service, value.chain) for feature, value in self.features.items()),
        ]

    def validate_capability_overrides(self) -> None:
        for service, cards in self.chains():
            for card in cards:
                if (
                    service == "image_gen"
                    and card.provider == "local"
                    and (card.api_key or card.model_name not in {"", "qwen"})
                ):
                    raise ValueError("本地生图使用固定 Qwen 工作流，不支持独立 API Key 或自选模型名称")

    @model_validator(mode="after")
    def validate_unique_providers(self) -> Self:
        provider_names = [card.provider for card in self.providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("同一供应商只能配置一张信息卡片")
        for service, cards in self.chains():
            if service == "embedding" and len(cards) > 1:
                raise ValueError("记忆向量固定使用同一模型，仅配置一张卡片；失败时使用关键词召回")
            capability_names = [card.provider for card in cards]
            if len(capability_names) != len(set(capability_names)):
                raise ValueError(f"{service} 中同一供应商只能配置一张卡片")
            for card in cards:
                if service == "llm" and card.context_window is None:
                    raise ValueError("请填写 LLM 的上下文窗口大小（正整数 tokens）")
                if service != "llm" and card.context_window is not None:
                    raise ValueError("上下文窗口仅支持 LLM 能力卡片")
        return self


class ProviderCardUpdate(ProviderCard):
    api_key_set: bool = False
    clear_api_key: bool = False


class CapabilityCardUpdate(CapabilityCard):
    api_key_set: bool = False
    clear_api_key: bool = False


class CapabilityChainsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: list[CapabilityCardUpdate] = Field(default_factory=list)
    stt: list[CapabilityCardUpdate] = Field(default_factory=list)
    tts: list[CapabilityCardUpdate] = Field(default_factory=list)
    image_gen: list[CapabilityCardUpdate] = Field(default_factory=list)
    video_gen: list[CapabilityCardUpdate] = Field(default_factory=list)
    embedding: list[CapabilityCardUpdate] = Field(default_factory=list)


class AIConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCardUpdate]
    capabilities: CapabilityChainsUpdate
    features: dict[AiFeature, FeatureChain[CapabilityCardUpdate]] = Field(default_factory=dict)


class ProviderCardPublic(ProviderCard):
    api_key_set: bool = False


class CapabilityCardPublic(CapabilityCard):
    api_key_set: bool = False


class CapabilityChainsPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: list[CapabilityCardPublic] = Field(default_factory=list)
    stt: list[CapabilityCardPublic] = Field(default_factory=list)
    tts: list[CapabilityCardPublic] = Field(default_factory=list)
    image_gen: list[CapabilityCardPublic] = Field(default_factory=list)
    video_gen: list[CapabilityCardPublic] = Field(default_factory=list)
    embedding: list[CapabilityCardPublic] = Field(default_factory=list)


class AIConfigPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCardPublic] = Field(default_factory=list)
    capabilities: CapabilityChainsPublic = Field(default_factory=CapabilityChainsPublic)
    features: dict[AiFeature, FeatureChain[CapabilityCardPublic]] = Field(default_factory=dict)
