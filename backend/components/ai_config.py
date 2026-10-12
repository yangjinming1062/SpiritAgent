from collections.abc import Iterator
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai_features import AI_FEATURE_BY_ID, AiFeature
from .ai_models import ModelModality

CAPABILITY_SERVICES = ("llm", "stt", "tts", "image_gen", "video_gen", "embedding")


class ProviderCard(BaseModel):
    """供应商实例：显示名称与注册适配器类型分开，凭据只属于此层。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID
    name: str = Field(min_length=1, max_length=128)
    provider_type: str = Field(min_length=1, max_length=64)
    api_key: str = Field(default="", max_length=2048)
    base_url: str = Field(default="", max_length=2048)


class CapabilityCard(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID
    provider_id: UUID
    model_name: str = Field(min_length=1, max_length=128)
    context_window: int | None = Field(default=None, gt=0, le=9_007_199_254_740_991, strict=True)
    max_output_tokens: int | None = Field(default=None, gt=0, le=9_007_199_254_740_991, strict=True)
    max_input_tokens: int | None = Field(default=None, gt=0, le=9_007_199_254_740_991, strict=True)
    input_modalities: frozenset[ModelModality] = frozenset({ModelModality.text})
    output_modalities: frozenset[ModelModality] = frozenset({ModelModality.text})


class CapabilityChains(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: list[CapabilityCard] = Field(default_factory=list)
    stt: list[CapabilityCard] = Field(default_factory=list)
    tts: list[CapabilityCard] = Field(default_factory=list)
    image_gen: list[CapabilityCard] = Field(default_factory=list)
    video_gen: list[CapabilityCard] = Field(default_factory=list)
    embedding: list[CapabilityCard] = Field(default_factory=list)


class FeatureChain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: list[UUID] = Field(default_factory=list)
    attempt_timeout_seconds: float | None = Field(default=None, gt=0, le=86400, allow_inf_nan=False)
    total_timeout_seconds: float | None = Field(default=None, gt=0, le=86400, allow_inf_nan=False)


class AIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCard] = Field(default_factory=list)
    capabilities: CapabilityChains = Field(default_factory=CapabilityChains)
    features: dict[AiFeature, FeatureChain] = Field(default_factory=dict)

    def models(self) -> Iterator[tuple[str, CapabilityCard]]:
        for service in CAPABILITY_SERVICES:
            for card in getattr(self.capabilities, service):
                yield service, card

    def find_provider(self, provider_id: UUID, system: Self | None = None) -> ProviderCard | None:
        return next((card for card in self.providers if card.id == provider_id), None) or (
            system.find_provider(provider_id) if system is not None else None
        )

    def find_model(
        self,
        model_id: UUID,
        system: Self | None = None,
    ) -> tuple[str, CapabilityCard, Self] | None:
        return next(((service, card, self) for service, card in self.models() if card.id == model_id), None) or (
            system.find_model(model_id) if system is not None else None
        )

    def validate_references(self, system: Self | None = None) -> None:
        if system is not None:
            own_ids = {card.id for card in self.providers} | {card.id for _, card in self.models()}
            system_ids = {card.id for card in system.providers} | {card.id for _, card in system.models()}
            if own_ids & system_ids:
                raise ValueError("用户配置的 ID 不能覆盖系统供应商或模型卡片")
        for service, card in self.models():
            provider = self.find_provider(card.provider_id, system)
            if provider is None:
                raise ValueError(f"{service} 模型 {card.model_name} 引用的供应商不存在：{card.provider_id}")
            if service == "image_gen" and provider.provider_type == "local" and card.model_name != "qwen":
                raise ValueError("本地生图使用固定 Qwen 工作流，不支持自选模型名称")
        for feature, value in self.features.items():
            definition = AI_FEATURE_BY_ID[feature]
            for model_id in value.chain:
                model = self.find_model(model_id, system)
                if model is None:
                    raise ValueError(f"功能 {definition.label} 引用的模型卡片不存在：{model_id}")
                if model[0] != definition.service:
                    raise ValueError(f"功能 {definition.label} 只能引用 {definition.service} 模型卡片")

    @model_validator(mode="after")
    def validate_cards(self) -> Self:
        ids = [card.id for card in self.providers] + [card.id for _, card in self.models()]
        if len(ids) != len(set(ids)):
            raise ValueError("供应商与模型卡片的 ID 不能重复")
        for service, card in self.models():
            if service == "llm":
                if card.context_window is None or card.max_output_tokens is None:
                    raise ValueError("请填写 LLM 的上下文窗口与输出 Token 上限（正整数 tokens）")
                if ModelModality.text not in card.output_modalities:
                    raise ValueError("LLM 模型须支持文字输出")
            if card.context_window is not None:
                if card.max_output_tokens is not None and card.max_output_tokens > card.context_window:
                    raise ValueError("输出 Token 上限不能超过上下文窗口")
                if card.max_input_tokens is not None and card.max_input_tokens > card.context_window:
                    raise ValueError("输入 Token 上限不能超过上下文窗口")
        for feature, value in self.features.items():
            if len(value.chain) != len(set(value.chain)):
                raise ValueError(f"功能 {AI_FEATURE_BY_ID[feature].label} 不能重复引用同一模型卡片")
            if AI_FEATURE_BY_ID[feature].service == "embedding" and len(value.chain) > 1:
                raise ValueError("记忆向量只能选择一个模型；失败时使用关键词召回")
        return self


class ProviderCardUpdate(ProviderCard):
    api_key_set: bool = False
    clear_api_key: bool = False


class AIConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCardUpdate]
    capabilities: CapabilityChains
    features: dict[AiFeature, FeatureChain] = Field(default_factory=dict)


class ProviderCardPublic(ProviderCard):
    api_key_set: bool = False


class AIConfigPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCardPublic] = Field(default_factory=list)
    capabilities: CapabilityChains = Field(default_factory=CapabilityChains)
    features: dict[AiFeature, FeatureChain] = Field(default_factory=dict)


class ProviderDefaults(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str
    requires_api_key: bool
    voice_design_model: str | None = None
    input_modalities: frozenset[ModelModality] = frozenset()


class ModelDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: ProviderCardUpdate
    user_id: int | None = Field(default=None, gt=0)
    service: Literal["llm", "stt", "tts", "image_gen", "video_gen", "embedding"]
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    catalog_id: str | None = Field(default=None, min_length=1, max_length=256)
    refresh: bool = False
