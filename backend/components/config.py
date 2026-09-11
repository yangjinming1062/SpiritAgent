import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .ai_config import AIConfig

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _parse_toml_dict(content: dict) -> dict[str, Any]:
    flat = {}
    for section, values in content.items():
        if isinstance(values, dict):
            for k, v in values.items():
                flat[k] = v
                flat[k.upper()] = v
        else:
            flat[section] = values
            flat[section.upper()] = values

    return flat


class TomlConfigSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        self._settings_dict: dict[str, Any] = {}

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        val = self._settings_dict.get(field_name)
        return val, field_name, False

    def prepare_field_value(self, field_name: str, field: Any, value: Any, value_is_complex: bool) -> Any:
        return value

    def __call__(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        example_toml = BACKEND_DIR / "config.toml.example"
        if example_toml.exists():
            with open(example_toml, "rb") as f:
                merged.update(_parse_toml_dict(tomllib.load(f)))

        user_toml = BACKEND_DIR / "config.toml"
        if user_toml.exists():
            with open(user_toml, "rb") as f:
                merged.update(_parse_toml_dict(tomllib.load(f)))

        self._settings_dict = merged
        return merged


class Settings(BaseSettings):
    ai_config: AIConfig = Field(default_factory=AIConfig)
    app_name: str = Field(default="SpiritAgent Backend", validation_alias="APP_NAME")
    api_prefix: str = Field(default="/api", validation_alias="API_PREFIX")
    # 后端可被供应商公网访问的基础地址（如 https://example.com）；非空时聊天视频附件以绝对 URL
    # 直发供应商（供应商自行拉取，单文件上限=会话配额），留空走 base64 内联（单文件 50MB 上限）。
    public_base_url: str = Field(default="", validation_alias="PUBLIC_BASE_URL")

    database_url: str

    jwt_secret_key: str = Field(min_length=16)
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=480, validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    admin_username: str = Field(default="spiritagent", validation_alias="ADMIN_USERNAME")
    admin_password: str = Field(default="spiritagent@admin123", validation_alias="ADMIN_PASSWORD")

    temp_file_ttl_hours: int = Field(default=24, validation_alias="TEMP_FILE_TTL_HOURS")
    data_dir: str = Field(default="./data", validation_alias="DATA_DIR")

    image_to_3d_provider: str = Field(default="tripo", validation_alias="IMAGE_TO_3D_PROVIDER")
    image_to_3d_poll_interval_seconds: float = Field(default=5.0, validation_alias="IMAGE_TO_3D_POLL_INTERVAL_SECONDS")
    image_to_3d_poll_backoff_max_seconds: float = Field(
        default=40.0,
        validation_alias="IMAGE_TO_3D_POLL_BACKOFF_MAX_SECONDS",
    )
    image_to_3d_max_poll_seconds: float = Field(default=1800.0, validation_alias="IMAGE_TO_3D_MAX_POLL_SECONDS")

    tripo_api_key: str = Field(default="", validation_alias="TRIPO_API_KEY")
    tripo_base_url: str = Field(default="https://openapi.tripo3d.ai/v3", validation_alias="TRIPO_BASE_URL")
    tripo_model_version: str = Field(default="v3.1-20260211", validation_alias="TRIPO_MODEL_VERSION")
    tripo_face_limit: int = Field(default=2000000, validation_alias="TRIPO_FACE_LIMIT")
    tripo_texture_quality: str = Field(default="extreme", validation_alias="TRIPO_TEXTURE_QUALITY")
    tripo_geometry_quality: str = Field(default="detailed", validation_alias="TRIPO_GEOMETRY_QUALITY")
    tripo_enable_autofix: bool = Field(default=True, validation_alias="TRIPO_ENABLE_AUTOFIX")

    hunyuan_api_key: str = Field(default="", validation_alias="HUNYUAN_API_KEY")
    hunyuan_base_url: str = Field(default="https://tokenhub.tencentmaas.com", validation_alias="HUNYUAN_BASE_URL")
    hunyuan_model_version: str = Field(default="hy-3d-3.1", validation_alias="HUNYUAN_MODEL_VERSION")
    hunyuan_generate_type: str = Field(default="Normal", validation_alias="HUNYUAN_GENERATE_TYPE")
    hunyuan_face_count: int = Field(default=0, validation_alias="HUNYUAN_FACE_COUNT")
    hunyuan_enable_pbr: bool = Field(default=True, validation_alias="HUNYUAN_ENABLE_PBR")
    hunyuan_result_format: str = Field(default="GLB", validation_alias="HUNYUAN_RESULT_FORMAT")

    seethrough_space_base: str = Field(
        default="https://24yearsold-see-through-demo.hf.space/gradio_api",
        validation_alias="SEETHROUGH_SPACE_BASE",
    )
    seethrough_fallback_base: str = Field(
        default="https://studio-ljsabc-see-through.api-inference.modelscope.net/gradio_api",
        validation_alias="SEETHROUGH_FALLBACK_BASE",
    )
    seethrough_fallback_token: str = Field(default="", validation_alias="SEETHROUGH_FALLBACK_TOKEN")
    companion_asset_image_providers: Annotated[list[str], NoDecode] = Field(
        default=["gemini", "grok"],
        validation_alias="COMPANION_ASSET_IMAGE_PROVIDERS",
    )

    companion_asset_signing_key: str
    ssrf_allowed_cidrs: str = Field(default="", validation_alias="SSRF_ALLOWED_CIDRS")

    llm_request_timeout_seconds: float = Field(default=300.0, validation_alias="LLM_REQUEST_TIMEOUT_SECONDS")
    llm_stream_idle_timeout_seconds: float = Field(default=60.0, validation_alias="LLM_STREAM_IDLE_TIMEOUT_SECONDS")
    llm_max_retry_attempts: int = Field(default=3, validation_alias="LLM_MAX_RETRY_ATTEMPTS")
    llm_base_retry_delay: float = Field(default=5.0, validation_alias="LLM_BASE_RETRY_DELAY")
    llm_max_retry_delay: float = Field(default=60.0, validation_alias="LLM_MAX_RETRY_DELAY")

    video_gen_poll_interval_seconds: float = Field(default=5.0, validation_alias="VIDEO_GEN_POLL_INTERVAL_SECONDS")
    video_gen_poll_backoff_max_seconds: float = Field(
        default=40.0,
        validation_alias="VIDEO_GEN_POLL_BACKOFF_MAX_SECONDS",
    )
    video_gen_max_poll_seconds: float = Field(default=900.0, validation_alias="VIDEO_GEN_MAX_POLL_SECONDS")
    video_gen_tool_wait_seconds: float = Field(default=180.0, validation_alias="VIDEO_GEN_TOOL_WAIT_SECONDS")
    video_gen_download_max_bytes: int = Field(default=209715200, validation_alias="VIDEO_GEN_DOWNLOAD_MAX_BYTES")

    web_search_backend: str = Field(default="ddgs", validation_alias="WEB_SEARCH_BACKEND")
    web_extract_backend: str = Field(default="tavily", validation_alias="WEB_EXTRACT_BACKEND")
    brave_search_api_key: str = Field(default="", validation_alias="BRAVE_SEARCH_API_KEY")
    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")
    tavily_base_url: str = Field(default="", validation_alias="TAVILY_BASE_URL")

    context_compression_threshold: float = Field(default=0.70, validation_alias="CONTEXT_COMPRESSION_THRESHOLD")
    context_summary_target_tokens: int = Field(default=2000, validation_alias="CONTEXT_SUMMARY_TARGET_TOKENS")
    context_summary_max_input_messages: int = Field(default=30, validation_alias="CONTEXT_SUMMARY_MAX_INPUT_MESSAGES")
    enable_context_compression: bool = Field(default=True, validation_alias="ENABLE_CONTEXT_COMPRESSION")
    ipc_future_timeout_seconds: float = Field(default=300.0, validation_alias="IPC_FUTURE_TIMEOUT_SECONDS")
    chat_active_window_minutes: int = Field(default=30, validation_alias="CHAT_ACTIVE_WINDOW_MINUTES")

    default_llm_context_tokens: int = Field(default=1000000, gt=0, validation_alias="DEFAULT_LLM_CONTEXT_TOKENS")

    rate_limit_enabled: bool = Field(default=True, validation_alias="RATE_LIMIT_ENABLED")
    login_rate_limit_per_minute: int = Field(default=10, validation_alias="LOGIN_RATE_LIMIT_PER_MINUTE")
    llm_completion_rate_limit_per_minute: int = Field(
        default=60,
        validation_alias="LLM_COMPLETION_RATE_LIMIT_PER_MINUTE",
    )
    llm_completion_rate_limit_per_ip_per_minute: int = Field(
        default=200,
        validation_alias="LLM_COMPLETION_RATE_LIMIT_PER_IP_PER_MINUTE",
    )
    media_stt_rate_limit_per_minute: int = Field(default=20, validation_alias="MEDIA_STT_RATE_LIMIT_PER_MINUTE")
    media_tts_rate_limit_per_minute: int = Field(default=30, validation_alias="MEDIA_TTS_RATE_LIMIT_PER_MINUTE")
    media_video_rate_limit_per_minute: int = Field(default=10, validation_alias="MEDIA_VIDEO_RATE_LIMIT_PER_MINUTE")
    companion_avatar_generate_rate_limit_per_minute: int = Field(
        default=3,
        validation_alias="COMPANION_AVATAR_GENERATE_RATE_LIMIT_PER_MINUTE",
    )
    companion_model_generate_rate_limit_per_minute: int = Field(
        default=1,
        validation_alias="COMPANION_MODEL_GENERATE_RATE_LIMIT_PER_MINUTE",
    )
    companion_outfit_generate_rate_limit_per_hour: int = Field(
        default=1,
        validation_alias="COMPANION_OUTFIT_GENERATE_RATE_LIMIT_PER_HOUR",
    )
    room_llm_replace_per_24h: int = Field(default=1, validation_alias="ROOM_LLM_REPLACE_PER_24H")
    room_max_attempts: int = Field(default=3, validation_alias="ROOM_MAX_ATTEMPTS")
    room_history_keep: int = Field(default=5, validation_alias="ROOM_HISTORY_KEEP")
    moment_system_per_day: int = Field(default=5, validation_alias="MOMENT_SYSTEM_PER_DAY")
    moment_llm_per_day: int = Field(default=3, validation_alias="MOMENT_LLM_PER_DAY")
    diary_nightly_enabled: bool = Field(default=True, validation_alias="DIARY_NIGHTLY_ENABLED")
    rate_limit_storage_url: str = Field(default="", validation_alias="RATE_LIMIT_STORAGE_URL")

    metrics_enabled: bool = Field(default=True, validation_alias="METRICS_ENABLED")
    metrics_path: str = Field(default="/metrics", validation_alias="METRICS_PATH")
    metrics_auth_token: str = Field(default="", validation_alias="METRICS_AUTH_TOKEN")

    channels_turn_queue_max: int = Field(default=20, validation_alias="CHANNELS_TURN_QUEUE_MAX")
    channels_inbound_rate_per_minute: int = Field(default=20, validation_alias="CHANNELS_INBOUND_RATE_PER_MINUTE")
    channels_restart_backoff_seconds: float = Field(default=10.0, validation_alias="CHANNELS_RESTART_BACKOFF_SECONDS")
    weixin_reply_max_chars: int = Field(default=2000, validation_alias="WEIXIN_REPLY_MAX_CHARS")
    weixin_ilink_poll_timeout_seconds: float = Field(default=40.0, validation_alias="WEIXIN_ILINK_POLL_TIMEOUT_SECONDS")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
    )
    log_format: Literal["json", "text"] = Field(default="json", validation_alias="LOG_FORMAT")
    llm_debug_logging: bool = Field(default=False, validation_alias="LLM_DEBUG_LOGGING")
    llm_debug_max_chars: int = Field(default=4000, validation_alias="LLM_DEBUG_MAX_CHARS")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    def validate_runtime_update(self, mapping: dict[str, Any]) -> Self:
        values = self.model_dump(mode="python", round_trip=True)
        values.update(mapping)
        return type(self).model_validate(values, by_name=True)

    def apply_runtime_candidate(self, candidate: Self) -> None:
        self.__dict__.update({key: getattr(candidate, key) for key in type(self).model_fields})

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, dotenv_settings, TomlConfigSource(settings_cls))

    @field_validator("companion_asset_image_providers", mode="before")
    @classmethod
    def _parse_providers_csv(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("data_dir", mode="after")
    @classmethod
    def _resolve_data_dir(cls, v: str) -> str:
        p = Path(v)
        if not p.is_absolute():
            return str((BACKEND_DIR / p).resolve())
        return str(p.resolve())


SETTINGS = Settings()
