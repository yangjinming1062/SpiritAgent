from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ModelModality(StrEnum):
    text = "text"
    image = "image"
    audio = "audio"
    video = "video"
    embeddings = "embeddings"


SERVICE_OUTPUT_MODALITY: dict[str, ModelModality] = {
    "llm": ModelModality.text,
    "stt": ModelModality.text,
    "tts": ModelModality.audio,
    "image_gen": ModelModality.image,
    "video_gen": ModelModality.video,
    "embedding": ModelModality.embeddings,
}

# 各能力的最低输入要求：链解析与管理页卡片筛选共用；具体请求的媒体需求由调用方另传。
SERVICE_REQUIRED_INPUT_MODALITIES: dict[str, frozenset[ModelModality]] = {
    "llm": frozenset({ModelModality.text}),
    "stt": frozenset({ModelModality.audio}),
    "tts": frozenset({ModelModality.text}),
    "image_gen": frozenset({ModelModality.text}),
    "video_gen": frozenset({ModelModality.text}),
    "embedding": frozenset({ModelModality.text}),
}


class ModelMetadata(BaseModel):
    """目录建议值；保存后的模型卡片才是调用配置。None 表示接口未提供该字段。"""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    display_name: str = ""
    services: list[str] = Field(default_factory=list)
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: list[ModelModality] | None = None
    output_modalities: list[ModelModality] | None = None
    catalog_id: str | None = None
    canonical_slug: str | None = None
    catalog_url: str | None = None
    field_sources: dict[str, str] = Field(default_factory=dict)


class ModelDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ModelMetadata] = Field(default_factory=list)
    candidates: dict[str, list[ModelMetadata]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    unsupported: bool = False
    catalog_updated_at: str | None = None
