import logging
import re

from .config import cfg_get, is_truthy_value, load_config

logger = logging.getLogger(__name__)

_PREFIX_PATTERNS: tuple[str, ...] = (
    r"sk-[A-Za-z0-9_-]{10,}",
    r"ghp_[A-Za-z0-9]{10,}",
    r"github_pat_[A-Za-z0-9_]{10,}",
    r"gho_[A-Za-z0-9]{10,}",
    r"ghu_[A-Za-z0-9]{10,}",
    r"ghs_[A-Za-z0-9]{10,}",
    r"ghr_[A-Za-z0-9]{10,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"AIza[A-Za-z0-9_-]{30,}",
    r"pplx-[A-Za-z0-9]{10,}",
    r"fal_[A-Za-z0-9_-]{10,}",
    r"fc-[A-Za-z0-9]{10,}",
    r"bb_live_[A-Za-z0-9_-]{10,}",
    r"gAAAA[A-Za-z0-9_=-]{20,}",
    r"AKIA[A-Z0-9]{16}",
    r"sk_live_[A-Za-z0-9]{10,}",
    r"sk_test_[A-Za-z0-9]{10,}",
    r"rk_live_[A-Za-z0-9]{10,}",
    r"SG\.[A-Za-z0-9_-]{10,}",
    r"hf_[A-Za-z0-9]{10,}",
    r"r8_[A-Za-z0-9]{10,}",
    r"npm_[A-Za-z0-9]{10,}",
    r"pypi-[A-Za-z0-9_-]{10,}",
    r"dop_v1_[A-Za-z0-9]{10,}",
    r"doo_v1_[A-Za-z0-9]{10,}",
    r"am_[A-Za-z0-9_-]{10,}",
    r"sk_[A-Za-z0-9_]{10,}",
    r"tvly-[A-Za-z0-9]{10,}",
    r"exa_[A-Za-z0-9]{10,}",
    r"gsk_[A-Za-z0-9]{10,}",
    r"xai-[A-Za-z0-9]{30,}",
)
# \b 锚定 token 两端，让位于字符串首尾或紧跟在 `=` / `"` / 空格后的凭据也能匹配（lookbehind/lookahead 无法锚字符串边界）。代价：前导为 `_` 的 token 会被跳过。
SECRET_PREFIX_RE = re.compile(r"\b(" + "|".join(_PREFIX_PATTERNS) + r")\b")

_SECRET_ENV_NAMES = r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|BEARER)"
_ENV_ASSIGN_RE = re.compile(
    rf"([A-Za-z_][A-Za-z0-9_]{{0,50}}{_SECRET_ENV_NAMES}[A-Za-z0-9_]{{0,50}})\s*=\s*(['\"]?)(\S+)\2",
)

_JSON_KEY_NAMES = (
    r"(?:api_?[Kk]ey|token|secret|password|access_token|refresh_token|"
    r"auth_token|bearer|secret_value|raw_secret|secret_input|key_material|"
    r"anthropic_api_key|openai_api_key|github_token)"
)
_JSON_FIELD_RE = re.compile(rf'("{_JSON_KEY_NAMES}")\s*:\s*"([^"]+)"', re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE)
_BEARER_RE = re.compile(r"\b([Bb]earer\s+)([A-Za-z0-9_.=-]{20,})")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----")
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@]+)(@)",
    re.IGNORECASE,
)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){0,2}")
_URL_USERINFO_RE = re.compile(r"(https?|wss?|ftp)://([^/\s:@]+):([^/\s@]+)@")


def _mask_token(token: str) -> str:
    return f"{token[:6]}...{token[-4:]}" if token and len(token) >= 18 else "***"


def _is_redact_enabled() -> bool:
    return is_truthy_value(cfg_get(load_config(), "security", "redact_secrets", default=True))


def redact_sensitive_text(text: str) -> str:
    """脱敏 *text* 中疑似凭据；由 ``security.redact_secrets`` 配置控制总开关。"""
    if not text or not _is_redact_enabled():
        return text
    return _redact(text)


def _redact(text: str) -> str:
    out = _PRIVATE_KEY_RE.sub("***PRIVATE_KEY***", text)
    out = _DB_CONNSTR_RE.sub(r"\1***\3", out)
    out = _URL_USERINFO_RE.sub(r"\1://\2:***@", out)
    out = _ENV_ASSIGN_RE.sub(lambda m: f"{m.group(1)}={m.group(2)}***{m.group(2)}", out)
    out = _JSON_FIELD_RE.sub(lambda m: f'{m.group(1)}: "***"', out)
    out = _AUTH_HEADER_RE.sub(r"\1***", out)
    out = _BEARER_RE.sub(lambda m: f"{m.group(1)}***", out)
    out = _JWT_RE.sub("***", out)
    return SECRET_PREFIX_RE.sub(lambda m: _mask_token(m.group(0)), out)
