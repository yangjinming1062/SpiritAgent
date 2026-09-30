# JSON-RPC 2.0 标准错误码；-32000..-32099 为 Slash 命令扩展区间。
JSON_RPC_VERSION: str = "2.0"
JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600
JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_INVALID_PARAMS: int = -32602
JSONRPC_INTERNAL_ERROR: int = -32603
JSONRPC_SLASH_CONFIRM_REQUIRED: int = -32001  # 未传 confirmed=true；失败 data 触发前端 confirm。
JSONRPC_SLASH_BUSY: int = -32002  # 回合生成中拒绝命令。
JSONRPC_SLASH_GENERIC: int = -32003  # handler 未映射异常兜底。

# LLM 预算、温度边界与标题/摘要回退值。
LLM_MAX_OUTPUT_TOKENS: int = 8192  # 含推理与正文。
TEMPERATURE_MIN: float = 0.0
TEMPERATURE_MAX: float = 1.0
CHAT_TEMPERATURE_DEFAULT: float = 0.7
TITLE_GENERATION_TEMPERATURE: float = 0.3
CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT: float = 0.0
TITLE_SNIPPET_MAX_CHARS: int = 500
TITLE_MAX_CHARS: int = 80  # 超出截断加 "..."。
DEFAULT_SESSION_TITLE: str = "New Conversation"  # 标题生成失败/跳过时的回退。
LLM_RETRY_MIN_TIMEOUT: float = 1.0
CONTEXT_SUMMARY_HEADROOM_FACTOR: int = 2  # 留余量避免中途截断。
TOOL_ENFORCE_OFF_VALUES: frozenset[str] = frozenset({"false", "never", "no", "off"})
TOOL_CALL_ID_HEX_PREFIX_LEN: int = 24  # 前 24 个 hex，96 bit 熵。
NIGHTLY_PLANNING_REASONING_EFFORT: str = "high"

# SessionSettingsPatch 可写三键 → 全局 UserSetting key。
SESSION_TO_GLOBAL_KEY_ALIASES: dict[str, str] = {
    "reasoning_effort": "agent.reasoning_effort",
    "temperature": "agent.temperature",
    "context_compression_threshold": "chat.context_compression_threshold",
}

# 附件、语音与生成资产：协议判别与供应商硬限。
ATTACHMENT_TYPE_IMAGE: str = "image"  # 视觉模型以 image_url 消费。
ATTACHMENT_TYPE_VIDEO: str = "video"  # 上传后以 input_video 消费。
# data URL 字符上限：覆盖 base64 膨胀（4/3），守住 WS 单帧与 DB TEXT 行体量。
ATTACHMENT_DATA_URL_MAX_CHARS: int = 12 * 1024 * 1024
ATTACHMENT_VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mov"})  # 供应商仅接受 mp4/mov。
ATTACHMENT_VIDEO_MAX_BYTES: int = 50 * 1024 * 1024  # 本地模式（无 public_base_url）内联上限。
VIDEO_INLINE_MAX_PER_REQUEST: int = 2  # 更早回合视频降级 [video] 占位。
TTS_MAX_TEXT_CHARS: int = 4_000  # OpenAI TTS 硬限 4096，留安全余量。
STT_MAX_AUDIO_BYTES: int = 24 * 1024 * 1024  # 云端 25 MB；客户端卡 24 MB 避 413。
MAX_VOICE_DESIGN_PROMPT_CHARS: int = 200  # MIMO 会逐字嵌入 voice_id。
REMOTE_ASSET_DOWNLOAD_MAX_BYTES: int = 50 * 1024 * 1024  # 超时按链路单独设定。
SCENE_DOWNLOAD_MAX_BYTES: int = 20 * 1024 * 1024  # 背景等小体积资产。

# 会话展示、重连重水化与登录心跳。
SESSION_PREVIEW_MAX_CHARS: int = 200
SESSION_HISTORY_TRUNCATE_THRESHOLD: int = 5000  # 重连重水化防御性负载截断。
SESSION_HISTORY_PRE_BUFFER: int = 200
LOGIN_HEARTBEAT_INTERVAL_SECONDS: int = 60

# 输入处理与脱敏防护。
SQL_LIKE_ESCAPE_CHAR: str = "\\"  # 转义 %、_，避免字面输入放大为「全部」。
SEARCH_INPUT_MAX_LEN: int = 100
REDACT_PHONE_DIGIT_THRESHOLD: int = 8  # 至少 8 位数字才按手机号脱敏。
SECRET_MASK_HEAD_CHARS: int = 6
SECRET_MASK_TAIL_CHARS: int = 4
SECRET_MASK_MIN_LENGTH: int = 18  # 低于此长度统一返 ***。

# 用户语言：默认中文，集合外回退 DEFAULT_LANGUAGE。
DEFAULT_LANGUAGE: str = "zh"
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"zh", "en"})
