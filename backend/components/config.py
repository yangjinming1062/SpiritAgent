import tomllib
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from sqlalchemy import URL

from .ai_config import AIConfig
from .constants import ATTACHMENT_VIDEO_MAX_BYTES

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


_EXAMPLE_CONFIG_PATH = BACKEND_DIR / "config.toml.example"
# 示例文件随仓库公开：这些项仍为空或示例值时等于公开密钥，web 进程拒绝启动。
_SECRET_SETTING_KEYS = ("jwt_secret_key", "companion_asset_signing_key", "admin_password")


def _load_toml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return _parse_toml_dict(tomllib.load(f))


class TomlConfigSource(PydanticBaseSettingsSource):
    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False  # 值由 __call__ 整份扁平字典返回。

    def __call__(self) -> dict[str, Any]:
        return _load_toml_file(_EXAMPLE_CONFIG_PATH) | _load_toml_file(BACKEND_DIR / "config.toml")


class _PostgresSettings(BaseSettings):
    user: str = Field(min_length=1, validation_alias="POSTGRES_USER")
    password: str = Field(min_length=1, validation_alias="POSTGRES_PASSWORD")
    database: str = Field(min_length=1, validation_alias="POSTGRES_DB")
    host: str = Field(default="postgres", min_length=1, validation_alias="POSTGRES_HOST")
    port: int = Field(default=5432, ge=1, le=65535, validation_alias="POSTGRES_PORT")

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )


def _postgres_database_url() -> str:
    settings = _PostgresSettings()
    return URL.create(
        "postgresql+psycopg",
        username=settings.user,
        password=settings.password,
        host=settings.host,
        port=settings.port,
        database=settings.database,
    ).render_as_string(hide_password=False)


class Settings(BaseSettings):
    ai_config: AIConfig = Field(default_factory=AIConfig)
    app_name: str = Field(default="SpiritAgent Backend", validation_alias="APP_NAME")
    # 非空时视频附件以绝对 URL 直发供应商（单文件上限=会话配额）；留空走 base64 内联（50MB）。
    public_base_url: str = Field(default="", validation_alias="PUBLIC_BASE_URL")

    @property
    def video_attachment_max_bytes(self) -> int:
        """视频附件单文件上限：公网模式（public_base_url 非空）= 会话配额（供应商直拉 URL）；本地模式 = 内联 50MB。"""
        return self.attachment_session_quota_bytes if self.public_base_url.strip() else ATTACHMENT_VIDEO_MAX_BYTES

    database_url: str = Field(default_factory=_postgres_database_url)

    jwt_secret_key: str = Field(min_length=16)
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(
        default=480,
        gt=0,
        # 上界约 10 年：到期时间 = 当前时间 + 该值，过大会超出 datetime 范围。
        le=5_256_000,
        validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    admin_username: str = Field(default="spiritagent", validation_alias="ADMIN_USERNAME")
    admin_password: str = Field(default="spiritagent@admin123", validation_alias="ADMIN_PASSWORD")

    temp_file_ttl_hours: int = Field(default=24, gt=0, validation_alias="TEMP_FILE_TTL_HOURS")
    data_dir: str = Field(default="./data", validation_alias="DATA_DIR")
    # 本地 ISNet 抠像模型（data_dir/models/<name>.onnx）。
    matting_model: str = Field(default="isnet-general-use", validation_alias="MATTING_MODEL")

    companion_asset_signing_key: str = Field(min_length=32)
    # 默认关闭：DNS 污染 / fake-ip 代理易误拦正常出站，部署者权衡内网风险后自行开启。
    ssrf_guard_enabled: bool = Field(default=False, validation_alias="SSRF_GUARD_ENABLED")
    ssrf_allowed_cidrs: str = Field(default="", validation_alias="SSRF_ALLOWED_CIDRS")

    llm_request_timeout_seconds: float = Field(default=3000.0, gt=0, validation_alias="LLM_REQUEST_TIMEOUT_SECONDS")
    llm_stream_idle_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="LLM_STREAM_IDLE_TIMEOUT_SECONDS",
    )
    llm_max_retry_attempts: int = Field(default=3, ge=0, validation_alias="LLM_MAX_RETRY_ATTEMPTS")
    llm_base_retry_delay: float = Field(default=5.0, ge=0, validation_alias="LLM_BASE_RETRY_DELAY")
    llm_max_retry_delay: float = Field(default=60.0, ge=0, validation_alias="LLM_MAX_RETRY_DELAY")

    video_gen_poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        validation_alias="VIDEO_GEN_POLL_INTERVAL_SECONDS",
    )
    video_gen_poll_backoff_max_seconds: float = Field(
        default=40.0,
        gt=0,
        validation_alias="VIDEO_GEN_POLL_BACKOFF_MAX_SECONDS",
    )
    video_gen_max_poll_seconds: float = Field(default=900.0, gt=0, validation_alias="VIDEO_GEN_MAX_POLL_SECONDS")
    video_gen_tool_wait_seconds: float = Field(default=180.0, ge=0, validation_alias="VIDEO_GEN_TOOL_WAIT_SECONDS")
    video_gen_download_max_bytes: int = Field(
        default=209715200,
        gt=0,
        validation_alias="VIDEO_GEN_DOWNLOAD_MAX_BYTES",
    )

    web_search_backend: str = Field(default="ddgs", validation_alias="WEB_SEARCH_BACKEND")
    web_extract_backend: str = Field(default="tavily", validation_alias="WEB_EXTRACT_BACKEND")
    web_search_default_results: int = Field(default=5, gt=0, le=100, validation_alias="WEB_SEARCH_DEFAULT_RESULTS")
    brave_search_api_key: str = Field(default="", validation_alias="BRAVE_SEARCH_API_KEY")
    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")
    tavily_base_url: str = Field(default="", validation_alias="TAVILY_BASE_URL")

    # 与会话级覆盖同范围：会话设置中低于 0.3 的阈值按无效处理。
    context_compression_threshold: float = Field(
        default=0.70,
        ge=0.3,
        le=1,
        validation_alias="CONTEXT_COMPRESSION_THRESHOLD",
    )
    context_summary_target_tokens: int = Field(default=5000, gt=0, validation_alias="CONTEXT_SUMMARY_TARGET_TOKENS")
    enable_context_compression: bool = Field(default=True, validation_alias="ENABLE_CONTEXT_COMPRESSION")
    ipc_future_timeout_seconds: float = Field(default=720.0, gt=0, validation_alias="IPC_FUTURE_TIMEOUT_SECONDS")

    # 对话回合与陪伴交互节奏：控制工具循环上限、桌面互动的 LLM 成本窗口与主动行为的静默门槛。
    agent_max_loop_turns: int = Field(default=150, gt=0, validation_alias="AGENT_MAX_LOOP_TURNS")
    agent_turn_timeout_seconds: float = Field(default=1800.0, gt=0, validation_alias="AGENT_TURN_TIMEOUT_SECONDS")
    companion_idle_expression_min_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        validation_alias="COMPANION_IDLE_EXPRESSION_MIN_INTERVAL_SECONDS",
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
        # 自主回合可能调用本地慢速生图；实际执行仍受意图到期时间约束。
        default=3600.0,
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

    # 动作库：每日制作额度按用户本地日结算；评审不设日限额；创建/自动使用独立开关。
    action_autonomous_create_daily_limit: int = Field(
        default=2,
        gt=0,
        validation_alias="ACTION_AUTONOMOUS_CREATE_DAILY_LIMIT",
    )
    action_user_requested_create_daily_limit: int = Field(
        default=3,
        gt=0,
        validation_alias="ACTION_USER_REQUESTED_CREATE_DAILY_LIMIT",
    )
    action_autocreate_enabled: bool = Field(default=True, validation_alias="ACTION_AUTOCREATE_ENABLED")

    # 夜间整理窗口、规划与日记预算；调度器 tick 周期与用户 Cron 配额。
    nightly_window_start_hour: int = Field(default=0, ge=0, le=23, validation_alias="NIGHTLY_WINDOW_START_HOUR")
    nightly_window_end_hour: int = Field(default=5, ge=0, le=23, validation_alias="NIGHTLY_WINDOW_END_HOUR")
    nightly_scan_interval_seconds: float = Field(default=300.0, gt=0, validation_alias="NIGHTLY_SCAN_INTERVAL_SECONDS")
    nightly_planning_max_tokens: int = Field(default=128_000, gt=0, validation_alias="NIGHTLY_PLANNING_MAX_TOKENS")
    nightly_reflection_max_tokens: int = Field(default=8_000, gt=0, validation_alias="NIGHTLY_REFLECTION_MAX_TOKENS")
    reflection_max_content_chars: int = Field(default=1_000, gt=0, validation_alias="REFLECTION_MAX_CONTENT_CHARS")
    scheduler_interval_seconds: float = Field(default=60.0, gt=0, validation_alias="SCHEDULER_INTERVAL_SECONDS")
    cron_max_active_per_user: int = Field(default=10, gt=0, validation_alias="CRON_MAX_ACTIVE_PER_USER")

    default_llm_context_tokens: int = Field(default=1000000, gt=0, validation_alias="DEFAULT_LLM_CONTEXT_TOKENS")

    rate_limit_enabled: bool = Field(default=True, validation_alias="RATE_LIMIT_ENABLED")
    # 各端点限流项须为正数（0 会使该端点请求全部 429）；整体关闭限流用 rate_limit_enabled。
    login_rate_limit_per_minute: int = Field(default=10, gt=0, validation_alias="LOGIN_RATE_LIMIT_PER_MINUTE")
    llm_completion_rate_limit_per_minute: int = Field(
        default=60,
        gt=0,
        validation_alias="LLM_COMPLETION_RATE_LIMIT_PER_MINUTE",
    )
    llm_completion_rate_limit_per_ip_per_minute: int = Field(
        default=200,
        gt=0,
        validation_alias="LLM_COMPLETION_RATE_LIMIT_PER_IP_PER_MINUTE",
    )
    media_stt_rate_limit_per_minute: int = Field(default=20, gt=0, validation_alias="MEDIA_STT_RATE_LIMIT_PER_MINUTE")
    media_tts_rate_limit_per_minute: int = Field(default=30, gt=0, validation_alias="MEDIA_TTS_RATE_LIMIT_PER_MINUTE")
    media_video_rate_limit_per_minute: int = Field(
        default=10,
        gt=0,
        validation_alias="MEDIA_VIDEO_RATE_LIMIT_PER_MINUTE",
    )
    companion_avatar_generate_rate_limit_per_minute: int = Field(
        default=3,
        gt=0,
        validation_alias="COMPANION_AVATAR_GENERATE_RATE_LIMIT_PER_MINUTE",
    )
    companion_outfit_generate_rate_limit_per_hour: int = Field(
        default=1,
        gt=0,
        validation_alias="COMPANION_OUTFIT_GENERATE_RATE_LIMIT_PER_HOUR",
    )
    # 小于等于 0 表示不限额，因此不设下界。
    scene_llm_create_per_24h: int = Field(default=1, validation_alias="SCENE_LLM_CREATE_PER_24H")
    scene_store_max_attempts: int = Field(default=3, gt=0, validation_alias="SCENE_STORE_MAX_ATTEMPTS")
    # 动态每日配额为 0 时不允许发布。
    post_requested_per_day: int = Field(default=3, ge=0, validation_alias="POST_REQUESTED_PER_DAY")
    post_autonomous_per_day: int = Field(default=3, ge=0, validation_alias="POST_AUTONOMOUS_PER_DAY")
    diary_nightly_enabled: bool = Field(default=True, validation_alias="DIARY_NIGHTLY_ENABLED")

    # 附件的体积/条数配额。
    max_attachments_per_turn: int = Field(default=16, gt=0, validation_alias="MAX_ATTACHMENTS_PER_TURN")
    attachment_session_quota_bytes: int = Field(
        default=512 * 1024 * 1024,
        gt=0,
        validation_alias="ATTACHMENT_SESSION_QUOTA_BYTES",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
    )
    log_format: Literal["json", "text"] = Field(default="json", validation_alias="LOG_FORMAT")
    llm_debug_logging: bool = Field(default=False, validation_alias="LLM_DEBUG_LOGGING")
    llm_debug_max_chars: int = Field(default=4000, ge=0, validation_alias="LLM_DEBUG_MAX_CHARS")

    # .env 与 config.toml 一样按 backend 目录定位，不随进程工作目录变化。
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        allow_inf_nan=False,
    )

    def validate_runtime_update(self, mapping: dict[str, Any]) -> Self:
        values = self.model_dump(mode="python", round_trip=True)
        values.update(mapping)
        return type(self).model_validate(values, by_name=True)

    def apply_runtime_candidate(self, candidate: Self) -> None:
        self.__dict__.update({key: getattr(candidate, key) for key in type(self).model_fields})

    @model_validator(mode="after")
    def validate_nightly_window(self) -> Self:
        if self.nightly_window_start_hour == self.nightly_window_end_hour:
            raise ValueError("夜间窗口的开始和结束小时不能相同")
        return self

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


def insecure_secret_settings() -> list[str]:
    """返回仍为空或仍是示例值的密钥配置项（环境变量名）。"""
    example = _load_toml_file(_EXAMPLE_CONFIG_PATH)
    return [
        key.upper()
        for key in _SECRET_SETTING_KEYS
        if not (value := getattr(SETTINGS, key))
        or value == example.get(key)
        or value == Settings.model_fields[key].default
    ]
