import tomllib
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
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
    # 抠像 onnx 模型（data_dir/models/<name>.onnx）：背景不纯色时必需，纯色背景走色键不依赖。
    matting_model: str = Field(default="isnet-general-use", validation_alias="MATTING_MODEL")

    companion_asset_signing_key: str
    # 出站 SSRF 守卫总开关，默认关闭：DNS 污染 / fake-ip 代理等环境易误拦正常出站，
    # 由部署者权衡内网访问风险后自行开启。
    ssrf_guard_enabled: bool = Field(default=False, validation_alias="SSRF_GUARD_ENABLED")
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
    web_search_default_results: int = Field(default=5, gt=0, validation_alias="WEB_SEARCH_DEFAULT_RESULTS")
    brave_search_api_key: str = Field(default="", validation_alias="BRAVE_SEARCH_API_KEY")
    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")
    tavily_base_url: str = Field(default="", validation_alias="TAVILY_BASE_URL")

    context_compression_threshold: float = Field(default=0.70, validation_alias="CONTEXT_COMPRESSION_THRESHOLD")
    context_summary_target_tokens: int = Field(default=5000, validation_alias="CONTEXT_SUMMARY_TARGET_TOKENS")
    enable_context_compression: bool = Field(default=True, validation_alias="ENABLE_CONTEXT_COMPRESSION")
    ipc_future_timeout_seconds: float = Field(default=300.0, validation_alias="IPC_FUTURE_TIMEOUT_SECONDS")

    # 对话回合与陪伴交互节奏：控制工具循环上限、桌面互动的 LLM 成本窗口与主动行为的静默门槛。
    agent_max_loop_turns: int = Field(default=150, gt=0, validation_alias="AGENT_MAX_LOOP_TURNS")
    companion_check_affect_min_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        validation_alias="COMPANION_CHECK_AFFECT_MIN_INTERVAL_SECONDS",
    )
    companion_approach_cooldown_seconds: float = Field(
        default=1800.0,
        gt=0,
        validation_alias="COMPANION_APPROACH_COOLDOWN_SECONDS",
    )
    companion_contact_quiet_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="COMPANION_CONTACT_QUIET_SECONDS",
    )
    desktop_disconnect_grace_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias="DESKTOP_DISCONNECT_GRACE_SECONDS",
    )
    companion_max_pending_intents: int = Field(default=16, gt=0, validation_alias="COMPANION_MAX_PENDING_INTENTS")
    companion_min_turn_interval_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="COMPANION_MIN_TURN_INTERVAL_SECONDS",
    )
    companion_turn_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        validation_alias="COMPANION_TURN_TIMEOUT_SECONDS",
    )
    companion_max_loop_turns: int = Field(default=8, gt=0, validation_alias="COMPANION_MAX_LOOP_TURNS")

    # 记忆维护与召回注入的节流与预算。
    memory_review_interval_seconds: float = Field(
        default=6 * 3600.0,
        gt=0,
        validation_alias="MEMORY_REVIEW_INTERVAL_SECONDS",
    )
    memory_recall_max_content_chars: int = Field(
        default=8_000,
        gt=0,
        validation_alias="MEMORY_RECALL_MAX_CONTENT_CHARS",
    )
    memory_prompt_max_memories: int = Field(default=10, gt=0, validation_alias="MEMORY_PROMPT_MAX_MEMORIES")

    # 夜间整理窗口、规划与日记预算；调度器 tick 周期与用户 Cron 配额。
    nightly_window_start_hour: int = Field(default=0, ge=0, le=23, validation_alias="NIGHTLY_WINDOW_START_HOUR")
    nightly_window_end_hour: int = Field(default=5, ge=0, le=23, validation_alias="NIGHTLY_WINDOW_END_HOUR")
    nightly_scan_interval_seconds: float = Field(default=300.0, gt=0, validation_alias="NIGHTLY_SCAN_INTERVAL_SECONDS")
    nightly_planning_max_tokens: int = Field(default=128_000, gt=0, validation_alias="NIGHTLY_PLANNING_MAX_TOKENS")
    nightly_diary_max_tokens: int = Field(default=8_000, gt=0, validation_alias="NIGHTLY_DIARY_MAX_TOKENS")
    diary_max_content_chars: int = Field(default=1_000, gt=0, validation_alias="DIARY_MAX_CONTENT_CHARS")
    scheduler_interval_seconds: float = Field(default=60.0, gt=0, validation_alias="SCHEDULER_INTERVAL_SECONDS")
    cron_max_active_per_user: int = Field(default=10, gt=0, validation_alias="CRON_MAX_ACTIVE_PER_USER")

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
    companion_outfit_generate_rate_limit_per_hour: int = Field(
        default=1,
        validation_alias="COMPANION_OUTFIT_GENERATE_RATE_LIMIT_PER_HOUR",
    )
    scene_llm_create_per_24h: int = Field(default=1, validation_alias="SCENE_LLM_CREATE_PER_24H")
    scene_store_max_attempts: int = Field(default=3, validation_alias="SCENE_STORE_MAX_ATTEMPTS")
    moment_llm_per_day: int = Field(default=3, validation_alias="MOMENT_LLM_PER_DAY")
    moment_autonomous_per_day: int = Field(default=3, validation_alias="MOMENT_AUTONOMOUS_PER_DAY")
    diary_nightly_enabled: bool = Field(default=True, validation_alias="DIARY_NIGHTLY_ENABLED")
    rate_limit_storage_url: str = Field(default="", validation_alias="RATE_LIMIT_STORAGE_URL")

    # 附件与生成媒体的体积/条数配额。
    max_attachments_per_turn: int = Field(default=16, gt=0, validation_alias="MAX_ATTACHMENTS_PER_TURN")
    attachment_session_quota_bytes: int = Field(
        default=512 * 1024 * 1024,
        gt=0,
        validation_alias="ATTACHMENT_SESSION_QUOTA_BYTES",
    )
    journal_media_download_max_bytes: int = Field(
        default=100 * 1024 * 1024,
        gt=0,
        validation_alias="JOURNAL_MEDIA_DOWNLOAD_MAX_BYTES",
    )

    metrics_enabled: bool = Field(default=True, validation_alias="METRICS_ENABLED")
    metrics_path: str = Field(default="/metrics", validation_alias="METRICS_PATH")
    metrics_auth_token: str = Field(default="", validation_alias="METRICS_AUTH_TOKEN")

    channels_turn_queue_max: int = Field(default=20, validation_alias="CHANNELS_TURN_QUEUE_MAX")
    channels_inbound_rate_per_minute: int = Field(default=20, validation_alias="CHANNELS_INBOUND_RATE_PER_MINUTE")
    channels_restart_backoff_seconds: float = Field(default=10.0, validation_alias="CHANNELS_RESTART_BACKOFF_SECONDS")
    channels_delivery_max_attempts: int = Field(default=3, gt=0, validation_alias="CHANNELS_DELIVERY_MAX_ATTEMPTS")
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

    @field_validator("data_dir", mode="after")
    @classmethod
    def _resolve_data_dir(cls, v: str) -> str:
        p = Path(v)
        if not p.is_absolute():
            return str((BACKEND_DIR / p).resolve())
        return str(p.resolve())


SETTINGS = Settings()
