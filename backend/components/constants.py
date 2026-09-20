# 本文件只保留协议、安全与供应商硬限等「不是配置」的技术常量；
# 可调的业务参数一律在 components/config.py 的 Settings 声明，经管理端系统设置热更。

# JSON-RPC 2.0 协议：版本、标准错误码与 Slash 命令通道的 SpiritAgent 扩展错误码（-32000..-32099 区间留给应用层）。
JSON_RPC_VERSION: str = "2.0"
JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600
JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_INVALID_PARAMS: int = -32602
JSONRPC_INTERNAL_ERROR: int = -32603
# 命令要求 confirm 但客户端未传 confirmed=true，失败时携带 data 让前端弹 confirm。
JSONRPC_SLASH_CONFIRM_REQUIRED: int = -32001
# 回合仍在生成中，命令拒绝执行。
JSONRPC_SLASH_BUSY: int = -32002
# Slash handler 内部异常未映射时的兜底错误码。
JSONRPC_SLASH_GENERIC: int = -32003

# LLM 调用与对话链路：温度边界、标题生成、重试与摘要预算等代码级回退值。
# 归一化 [0,1] 刻度下的温度校验边界与各链路默认值。
TEMPERATURE_MIN: float = 0.0
TEMPERATURE_MAX: float = 1.0
CHAT_TEMPERATURE_DEFAULT: float = 0.7
TITLE_GENERATION_TEMPERATURE: float = 0.3
CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT: float = 0.0
TITLE_SNIPPET_MAX_CHARS: int = 500
# 标题超出时截断加 "..."。
TITLE_MAX_CHARS: int = 80
# LLM 标题生成失败或跳过时的回退标题。
DEFAULT_SESSION_TITLE: str = "New Conversation"
TITLE_GENERATION_MAX_TOKENS: int = 500
LLM_RETRY_MIN_TIMEOUT: float = 1.0
# 给 LLM 留出余量避免中途截断。
CONTEXT_SUMMARY_HEADROOM_FACTOR: int = 2
# 关闭 tool-use 约束的字符串值集合。
TOOL_ENFORCE_OFF_VALUES: frozenset[str] = frozenset({"false", "never", "no", "off"})
# 保留 tool_call id 的前 24 个 hex 字符（96 bit 熵）。
TOOL_CALL_ID_HEX_PREFIX_LEN: int = 24
# 夜间规划使用高推理档。
NIGHTLY_PLANNING_REASONING_EFFORT: str = "high"

# 会话级 setting key（SessionSettingsPatch 可写的三个键）→ 全局 UserSetting key 的映射。
SESSION_TO_GLOBAL_KEY_ALIASES: dict[str, str] = {
    "reasoning_effort": "agent.reasoning_effort",
    "temperature": "agent.temperature",
    "context_compression_threshold": "chat.context_compression_threshold",
}
# 用字符串 "true" 而不是 bool，以匹配 user_settings.get() 的比较模式。
BACKGROUND_REVIEW_DEFAULT: str = "true"

# 附件、语音与生成资产的协议判别与供应商硬限。
# 协议层附件类型判别；聊天管道接受 image（视觉模型以 image_url 消费）与 video（上传后以 input_video 消费）。
ATTACHMENT_TYPE_IMAGE: str = "image"
ATTACHMENT_TYPE_VIDEO: str = "video"
# 附件 data URL（``data:image/...;base64,``）的字符上限。桌面端本地图片以 data URL 直发多模态，
# 不走后端落盘；上限需覆盖 base64 膨胀（4/3），同时守住 WS 单帧与数据库 TEXT 行的体量。
ATTACHMENT_DATA_URL_MAX_CHARS: int = 12 * 1024 * 1024
# 视频附件容器白名单：供应商侧实测仅接受 mp4 / mov（webm 返回 video format not allowed）。
ATTACHMENT_VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mov"})
# 本地模式（未配置 ``public_base_url``）视频附件单文件上限；供应商以 base64 data URL 内联消费。
ATTACHMENT_VIDEO_MAX_BYTES: int = 50 * 1024 * 1024
# 单次供应商请求最多内联的视频个数；更早回合的视频降级为 [video] 文本占位（与旧图 [screenshot] 同构）。
VIDEO_INLINE_MAX_PER_REQUEST: int = 2
# OpenAI TTS 硬限 4096，留 4000 给安全余量。
TTS_MAX_TEXT_CHARS: int = 4_000
# 云端 STT 上限 25 MB；客户端卡到 24 MB 避开边界 413。
STT_MAX_AUDIO_BYTES: int = 24 * 1024 * 1024
# voice-design 自由文本提示词上限；MIMO 会把它逐字嵌入 voice_id。
MAX_VOICE_DESIGN_PROMPT_CHARS: int = 200
# 远端资产下载的统一体积上限（供应商 CDN 图、生成图、参考图）；各调用点的超时按链路单独设定。
REMOTE_ASSET_DOWNLOAD_MAX_BYTES: int = 50 * 1024 * 1024
# 房间背景等小体积资产图的下载上限。
ROOM_BACKDROP_DOWNLOAD_MAX_BYTES: int = 20 * 1024 * 1024

# 会话展示、重连重水化与登录心跳。
SESSION_PREVIEW_MAX_CHARS: int = 200
# 会话重连重水化历史时的防御性负载截断阈值及前向缓冲跨度。
SESSION_HISTORY_TRUNCATE_THRESHOLD: int = 5000
SESSION_HISTORY_PRE_BUFFER: int = 200
LOGIN_HEARTBEAT_INTERVAL_SECONDS: int = 60

# 输入处理与脱敏防护。
# SQL LIKE 通配符（%、_）的转义符，避免字面输入把搜索放大为「全部」。
SQL_LIKE_ESCAPE_CHAR: str = "\\"
SEARCH_INPUT_MAX_LEN: int = 100
# 至少 8 位数字才按手机号脱敏；密钥掩码保留首尾字符并设触发最小长度。
REDACT_PHONE_DIGIT_THRESHOLD: int = 8
SECRET_MASK_HEAD_CHARS: int = 6
SECRET_MASK_TAIL_CHARS: int = 4
SECRET_MASK_MIN_LENGTH: int = 18

# 面向用户的默认语言与合法语言集合。
# 默认用户面向语言为中文（让新装环境开口即中文），可通过 ``language`` UserSetting / 会话覆盖切到英文。
DEFAULT_LANGUAGE: str = "zh"
# ``language`` setting 的合法值集合；集合外回退到 DEFAULT_LANGUAGE。
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"zh", "en"})
