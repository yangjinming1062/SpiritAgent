from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CAPABILITY_SERVICES = ("llm", "stt", "tts", "image_gen", "video_gen", "embedding")


class ProviderCard(BaseModel):
    """供应商信息库卡片：只存密钥与地址，模型名属能力卡片。"""

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


class CapabilityChains(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: list[CapabilityCard] = Field(default_factory=list)
    stt: list[CapabilityCard] = Field(default_factory=list)
    tts: list[CapabilityCard] = Field(default_factory=list)
    image_gen: list[CapabilityCard] = Field(default_factory=list)
    video_gen: list[CapabilityCard] = Field(default_factory=list)
    embedding: list[CapabilityCard] = Field(default_factory=list)


class AIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCard] = Field(default_factory=list)
    capabilities: CapabilityChains = Field(default_factory=CapabilityChains)

    def validate_capability_overrides(self) -> None:
        for card in self.capabilities.image_gen:
            if card.provider == "local" and (card.api_key or card.model_name not in {"", "qwen"}):
                raise ValueError("本地生图使用固定 Qwen 工作流，不支持独立 API Key 或自选模型名称")

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
