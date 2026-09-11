from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CAPABILITY_SERVICES = ("llm", "stt", "tts", "image_gen", "video_gen")


class ProviderCard(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    api_key: str = Field(default="", max_length=2048)
    base_url: str = Field(default="", max_length=2048)
    model_name: str = Field(default="", max_length=128)


class CapabilityChains(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: list[ProviderCard] = Field(default_factory=list)
    stt: list[ProviderCard] = Field(default_factory=list)
    tts: list[ProviderCard] = Field(default_factory=list)
    image_gen: list[ProviderCard] = Field(default_factory=list)
    video_gen: list[ProviderCard] = Field(default_factory=list)


class AIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCard] = Field(default_factory=list)
    capabilities: CapabilityChains = Field(default_factory=CapabilityChains)

    @model_validator(mode="after")
    def validate_unique_providers(self) -> Self:
        provider_names = [card.provider for card in self.providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("同一供应商只能配置一张信息卡片")
        for service in CAPABILITY_SERVICES:
            capability_names = [card.provider for card in getattr(self.capabilities, service)]
            if len(capability_names) != len(set(capability_names)):
                raise ValueError(f"{service} 中同一供应商只能配置一张卡片")
        return self


class ProviderCardUpdate(ProviderCard):
    api_key_set: bool = False
    clear_api_key: bool = False


class CapabilityChainsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: list[ProviderCardUpdate] = Field(default_factory=list)
    stt: list[ProviderCardUpdate] = Field(default_factory=list)
    tts: list[ProviderCardUpdate] = Field(default_factory=list)
    image_gen: list[ProviderCardUpdate] = Field(default_factory=list)
    video_gen: list[ProviderCardUpdate] = Field(default_factory=list)


class AIConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCardUpdate]
    capabilities: CapabilityChainsUpdate


class ProviderCardPublic(ProviderCard):
    api_key_set: bool = False


class CapabilityChainsPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: list[ProviderCardPublic] = Field(default_factory=list)
    stt: list[ProviderCardPublic] = Field(default_factory=list)
    tts: list[ProviderCardPublic] = Field(default_factory=list)
    image_gen: list[ProviderCardPublic] = Field(default_factory=list)
    video_gen: list[ProviderCardPublic] = Field(default_factory=list)


class AIConfigPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCardPublic] = Field(default_factory=list)
    capabilities: CapabilityChainsPublic = Field(default_factory=CapabilityChainsPublic)


def load_ai_config(raw: dict[str, Any] | AIConfig | None) -> AIConfig:
    if isinstance(raw, AIConfig):
        return raw
    return AIConfig.model_validate(raw or {})
