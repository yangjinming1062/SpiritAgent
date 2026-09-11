import os
import re
from collections.abc import Callable

from .constants import (
    REDACT_PHONE_DIGIT_THRESHOLD,
    SECRET_MASK_HEAD_CHARS,
    SECRET_MASK_MIN_LENGTH,
    SECRET_MASK_TAIL_CHARS,
)

_SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "api_key",
        "apikey",
        "client_secret",
        "password",
        "auth",
        "jwt",
        "session",
        "secret",
        "key",
        "code",
        "signature",
        "x-amz-signature",
    },
)

# 导入时快照，避免运行时改 env（甚至 LLM 注入的 `export SPIRITAGENT_REDACT_SECRETS=false`）中途关闭脱敏。
_REDACT_ENABLED = os.getenv("SPIRITAGENT_REDACT_SECRETS", "true").lower() in {"1", "true", "yes", "on"}

_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{10,}",  # OpenAI / OpenRouter / Anthropic
    r"rk_(?:live|test)_[A-Za-z0-9]{10,}",  # Resend (alternative)
    r"DSN=[A-Za-z0-9_:/.\-@?&=%]+",  # Sentry DSN
    r"postgres(?:ql)?://[^:]+:[^@]+@[^/\s]+",  # Postgres connection string
    r"mongodb(?:\+srv)?://[^:]+:[^@]+@[^/\s]+",  # Mongo connection string
    r"redis://[^:]+:[^@]+@[^/\s]+",  # Redis connection string
    r"amqp://[^:]+:[^@]+@[^/\s]+",  # RabbitMQ connection string
    r"ghp_[A-Za-z0-9]{10,}",  # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{10,}",  # GitHub PAT (fine-grained)
    r"gho_[A-Za-z0-9]{10,}",  # GitHub OAuth
    r"ghu_[A-Za-z0-9]{10,}",  # GitHub user-to-server
    r"ghs_[A-Za-z0-9]{10,}",  # GitHub server-to-server
    r"ghr_[A-Za-z0-9]{10,}",  # GitHub refresh token
    r"xox[baprs]-[A-Za-z0-9-]{10,}",  # Slack
    r"AIza[A-Za-z0-9_-]{30,}",  # Google API keys
    r"pplx-[A-Za-z0-9]{10,}",  # Perplexity
    r"fal_[A-Za-z0-9_-]{10,}",  # Fal.ai
    r"fc-[A-Za-z0-9]{10,}",  # Firecrawl
    r"bb_live_[A-Za-z0-9_-]{10,}",  # BrowserBase
    r"gAAAA[A-Za-z0-9_=-]{20,}",  # Codex encrypted tokens
    r"AKIA[A-Z0-9]{16}",  # AWS Access Key ID
    r"sk_live_[A-Za-z0-9]{10,}",  # Stripe secret (live)
    r"sk_test_[A-Za-z0-9]{10,}",  # Stripe secret (test)
    r"rk_live_[A-Za-z0-9]{10,}",  # Stripe restricted key
    r"SG\.[A-Za-z0-9_-]{10,}",  # SendGrid
    r"hf_[A-Za-z0-9]{10,}",  # HuggingFace
    r"r8_[A-Za-z0-9]{10,}",  # Replicate
    r"npm_[A-Za-z0-9]{10,}",  # npm
    r"pypi-[A-Za-z0-9_-]{10,}",  # PyPI
    r"dop_v1_[A-Za-z0-9]{10,}",  # DigitalOcean PAT
    r"doo_v1_[A-Za-z0-9]{10,}",  # DigitalOcean OAuth
    r"am_[A-Za-z0-9_-]{10,}",  # AgentMail
    r"sk_[A-Za-z0-9_]{10,}",  # ElevenLabs (underscore)
    r"tvly-[A-Za-z0-9]{10,}",  # Tavily
    r"exa_[A-Za-z0-9]{10,}",  # Exa
    r"gsk_[A-Za-z0-9]{10,}",  # Groq
    r"syt_[A-Za-z0-9]{10,}",  # Matrix
    r"retaindb_[A-Za-z0-9]{10,}",  # RetainDB
    r"hsk-[A-Za-z0-9]{10,}",  # Hindsight
    r"mem0_[A-Za-z0-9]{10,}",  # Mem0
    r"brv_[A-Za-z0-9]{10,}",  # ByteRover
    r"xai-[A-Za-z0-9]{30,}",  # xAI (Grok)
]

_SECRET_ENV_NAMES = r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)"
_ENV_ASSIGN_RE = re.compile(rf"([A-Z0-9_]{{0,50}}{_SECRET_ENV_NAMES}[A-Z0-9_]{{0,50}})\s*=\s*(['\"]?)(\S+)\2")
_JSON_KEY_NAMES = r"(?:api_?[Kk]ey|token|secret|password|access_token|refresh_token|auth_token|bearer|secret_value|raw_secret|secret_input|key_material|connection_string|dsn)"
_JSON_FIELD_RE = re.compile(rf'("{_JSON_KEY_NAMES}")\s*:\s*"([^"]+)"', re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE)
_TELEGRAM_RE = re.compile(r"(bot)?(\d{8,}):([-A-Za-z0-9_]{30,})")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----")
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@]+)(@)",
    re.IGNORECASE,
)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){0,2}")
_SIGNAL_PHONE_RE = re.compile(r"(\+[1-9]\d{6,14})(?![A-Za-z0-9])")

# 只匹配「整段就是 k=v&k=v 形式体」的文本；Web URL 的 query string 故意不处理——magic link / OAuth 回调常走不透明 token，盲脱敏会破坏这些流程；URL 内的已知凭据形态仍由 _PREFIX_RE / _JWT_RE 兜底。
_FORM_BODY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*(?:&[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*)+$")
_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])")


def _redact_query_string(query: str) -> str:
    if not query:
        return query
    parts = []
    for pair in query.split("&"):
        if "=" not in pair:
            parts.append(pair)
            continue
        key, _, value = pair.partition("=")
        parts.append(f"{key}=***" if key.lower() in _SENSITIVE_QUERY_PARAMS else pair)
    return "&".join(parts)


def _redact_form_body(text: str) -> str:
    """仅当整段是干净的 form body 时脱敏。"""
    if not text or "\n" in text or "&" not in text:
        return text
    stripped = text.strip()
    return _redact_query_string(stripped) if _FORM_BODY_RE.match(stripped) else text


def _mask_secret(match_value: str) -> str:
    """短串（含空串）统一返占位——f-string 切头尾在短串上会撞索引并泄漏字符。
    与 runner/utils/redact.py::_mask_token 共用的脱敏形状；别处需要时也走这里。"""
    if len(match_value) < SECRET_MASK_MIN_LENGTH:
        return "***"
    return f"{match_value[:SECRET_MASK_HEAD_CHARS]}...{match_value[-SECRET_MASK_TAIL_CHARS:]}"


def _sub_prefix(m: re.Match) -> str:
    return _mask_secret(m.group(1))


def _sub_env(m: re.Match) -> str:
    return f"{m.group(1)}={m.group(2)}{_mask_secret(m.group(3))}{m.group(2)}"


def _sub_json(m: re.Match) -> str:
    return f'{m.group(1)}: "{_mask_secret(m.group(2))}"'


def _sub_auth(m: re.Match) -> str:
    return m.group(1) + _mask_secret(m.group(2))


def _sub_telegram(m: re.Match) -> str:
    return f"{m.group(1) or ''}{m.group(2)}:***"


def _sub_db_connstr(m: re.Match) -> str:
    return f"{m.group(1)}***{m.group(3)}"


def _sub_jwt(m: re.Match) -> str:
    return _mask_secret(m.group(0))


def _sub_phone(m: re.Match) -> str:
    phone = m.group(1)
    if len(phone) <= REDACT_PHONE_DIGIT_THRESHOLD:
        return phone[:2] + "****" + phone[-2:]
    return phone[:4] + "****" + phone[-4:]


def _sub_private_key(_m: re.Match) -> str:
    return "[REDACTED PRIVATE KEY]"


# 每条规则是 (substring gate, compiled pattern, substitution callback)；gate 在无凭据日志行上能省掉 95%+ 的 regex 执行，且每个 _PREFIX_PATTERNS 的 gated 子串都是字面前缀，不会漏报。
def _apply_gated_rules(text: str, rules: list[tuple[str, re.Pattern, Callable[[re.Match], str]]]) -> str:
    for gate, pattern, sub in rules:
        if gate in text:
            text = pattern.sub(sub, text)
    return text


_GATED_RULES_DEFAULT: list[tuple[str, re.Pattern, Callable[[re.Match], str]]] = [
    ("=", _ENV_ASSIGN_RE, _sub_env),
    (':"', _JSON_FIELD_RE, _sub_json),
    ("uthorization", _AUTH_HEADER_RE, _sub_auth),
    (":", _TELEGRAM_RE, _sub_telegram),
    ("BEGIN-----", _PRIVATE_KEY_RE, _sub_private_key),
    ("://", _DB_CONNSTR_RE, _sub_db_connstr),
    ("eyJ", _JWT_RE, _sub_jwt),
    ("+", _SIGNAL_PHONE_RE, _sub_phone),
]


def _extract_literal_prefix(pattern: str) -> str:
    """取正则 pattern 从开头到首个元字符的字面前缀。"""
    meta = "[(\\.?*+|{^$"
    for i, ch in enumerate(pattern):
        if ch in meta:
            return pattern[:i]
    return pattern


# 每条 prefix regex 都以这些字面量打头——廉价预筛，只有命中已知前缀子串才跑昂贵的 _PREFIX_RE。
_PREFIX_SUBSTRINGS = tuple(_extract_literal_prefix(p) for p in _PREFIX_PATTERNS)


def _has_known_prefix_substring(text: str) -> bool:
    return any(p in text for p in _PREFIX_SUBSTRINGS)


def redact_sensitive_text(text: str | None) -> str | None:
    """对文本应用所有脱敏 pattern；regex 都走 substring 预筛 gate，无凭据日志行全扫描降 ~68%。"""
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    if not _REDACT_ENABLED:
        return text

    if _has_known_prefix_substring(text):
        text = _PREFIX_RE.sub(_sub_prefix, text)

    text = _apply_gated_rules(text, _GATED_RULES_DEFAULT)

    if "&" in text and "=" in text:
        text = _redact_form_body(text)

    return text
