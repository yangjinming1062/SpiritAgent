import functools
import os
import re

# SpiritAgent 控制面文件：供应商凭据、OAuth token、HMAC 密钥、网关配置。按 basename 列入，~/.spiritagent/<name> 与 ~/.spiritagent/profiles/<profile>/<name> 都被拦截。
SPIRITAGENT_CONTROL_FILE_BASENAMES: tuple[str, ...] = (
    "auth.json",
    "auth.lock",
    "desktop-settings.json",
    "webhook_subscriptions.json",
    ".env",
)

# .env.example 故意不在此列——它是带文档结构的占位文件。
BLOCKED_PROJECT_ENV_BASENAMES: frozenset[str] = frozenset(
    {".env", ".env.local", ".env.development", ".env.production", ".env.test", ".env.staging", ".envrc"},
)

_WRITE_DENIED_RELATIVE_PATHS: tuple[tuple[str, ...], ...] = (
    (".ssh", "authorized_keys"),
    (".ssh", "id_rsa"),
    (".ssh", "id_ed25519"),
    (".ssh", "config"),
    (".bashrc",),
    (".zshrc",),
    (".profile",),
    (".bash_profile",),
    (".zprofile",),
    (".netrc",),
    (".pgpass",),
    (".npmrc",),
    (".pypirc",),
    (".git-credentials",),
)

_WRITE_DENIED_ABSOLUTE_PATHS: tuple[str, ...] = ("/etc/sudoers", "/etc/passwd", "/etc/shadow")

_WRITE_DENIED_PREFIXES_RELATIVE: tuple[tuple[str, ...], ...] = (
    (".ssh",),
    (".aws",),
    (".gnupg",),
    (".kube",),
    (".docker",),
    (".azure",),
    (".config", "gh"),
    (".config", "gcloud"),
)

_WRITE_DENIED_PREFIXES_ABSOLUTE: tuple[str, ...] = ("/etc/sudoers.d", "/etc/systemd")
_WRITE_DENIED_SPIRITAGENT_PREFIXES: tuple[str, ...] = ("pairing", "skills/.hub")

# 本进程跑在 Linux，而文件操作发生在 runner 机器（Windows 为主）：Windows 绝对路径在
# 本机 realpath 解析不出敏感段，需按盘符 + 用户目录切出尾部路径段，与同一套敏感清单比对。
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_PROFILE_PATH = re.compile(r"^[A-Za-z]:[\\/](?:users|documents and settings)[\\/][^\\/]+[\\/]", re.IGNORECASE)


def _windows_profile_tail(path: str) -> tuple[str, ...] | None:
    """Windows 用户目录内路径返回小写尾部路径段（去掉盘符与 Users/<name>）；其余返回 None。"""
    if not _WINDOWS_PATH.match(path):
        return None
    normalized = path.replace("\\", "/")
    if not _WINDOWS_PROFILE_PATH.match(normalized):
        return ()
    parts = tuple(p.lower() for p in normalized.split("/") if p)
    return parts[3:]


def _spiritagent_tail_denied(tail: tuple[str, ...]) -> bool:
    if tail[0] != ".spiritagent" or len(tail) < 2:
        return False
    if len(tail) == 2 and tail[1] in SPIRITAGENT_CONTROL_FILE_BASENAMES:
        return True
    return tail[1] == "pairing" or tail[1:3] == ("skills", ".hub")


@functools.lru_cache(maxsize=1)
def _spiritagent_home() -> str:
    """规范化的 ~/.spiritagent 路径，模块加载时缓存——home 运行时极少变动。"""
    return os.path.realpath(os.path.expanduser("~/.spiritagent"))


def _join_real(base: str, *parts: str) -> str:
    return os.path.realpath(os.path.join(base, *parts))


@functools.lru_cache(maxsize=4)
def _write_denied_paths(home: str) -> frozenset[str]:
    """绝不允许写入的精确敏感路径集合。"""
    home_real = os.path.realpath(home)
    spiritagent_home = _spiritagent_home()
    return frozenset(
        {
            *(_join_real(home_real, *parts) for parts in _WRITE_DENIED_RELATIVE_PATHS),
            *(_join_real(spiritagent_home, name) for name in SPIRITAGENT_CONTROL_FILE_BASENAMES),
            *(os.path.realpath(p) for p in _WRITE_DENIED_ABSOLUTE_PATHS),
        },
    )


@functools.lru_cache(maxsize=4)
def _write_denied_prefixes(home: str) -> tuple[str, ...]:
    """绝不允许写入的敏感目录前缀集合。"""
    home_real = os.path.realpath(home)
    spiritagent_home = _spiritagent_home()
    return tuple(
        p + os.sep
        for p in (
            *(_join_real(home_real, *parts) for parts in _WRITE_DENIED_PREFIXES_RELATIVE),
            *_WRITE_DENIED_PREFIXES_ABSOLUTE,
            *(_join_real(spiritagent_home, sub) for sub in _WRITE_DENIED_SPIRITAGENT_PREFIXES),
        )
    )


def is_write_denied(path: str) -> bool:
    """路径（解析符号链接后）命中写入黑名单时返回 True。"""
    text = str(path)
    if "\x00" in text:
        return True
    if _WINDOWS_PATH.match(text):
        tail = _windows_profile_tail(text)
        if not tail:
            return False
        if tail in _WRITE_DENIED_RELATIVE_PATHS:
            return True
        if any(tail[: len(prefix)] == prefix for prefix in _WRITE_DENIED_PREFIXES_RELATIVE):
            return True
        return _spiritagent_tail_denied(tail)
    try:
        resolved = os.path.realpath(os.path.expanduser(text))
    except (ValueError, OSError):
        # 嵌入 null 字节或非法路径字符永远无法合法写入，直接拒绝而非放行
        return True
    if resolved in _write_denied_paths(os.path.expanduser("~")):
        return True
    return any(resolved.startswith(prefix) for prefix in _write_denied_prefixes(os.path.expanduser("~")))


@functools.lru_cache(maxsize=4)
def _read_block_messages() -> tuple[tuple[str, str], ...]:
    """预解析的 (real_path, error_message) 凭据文件对。"""
    return tuple(
        (_join_real(_spiritagent_home(), name), f"Blocked: cannot read SpiritAgent credential file ({name}).")
        for name in SPIRITAGENT_CONTROL_FILE_BASENAMES
    )


@functools.lru_cache(maxsize=4)
def _read_block_prefixes() -> tuple[tuple[str, str], ...]:
    spiritagent_home = _spiritagent_home()
    return (
        (
            _join_real(spiritagent_home, "pairing") + os.sep,
            "Blocked: cannot read SpiritAgent credential directory (~/.spiritagent/pairing/).",
        ),
    )


def get_read_block_error(path: str) -> str | None:
    """拒绝读取目标时返回错误消息，路径可用时返回 None——仅为纵深防御，终端工具同用户运行 shell，决心攻击者仍可 `cat auth.json`；黑名单旨在让守规矩的模型止步于错误消息。"""
    text = str(path)
    if _WINDOWS_PATH.match(text):
        tail = _windows_profile_tail(text)
        if tail and _spiritagent_tail_denied(tail):
            return "Blocked: cannot read SpiritAgent credential file."
        basename = tail[-1] if tail else text.replace("\\", "/").rsplit("/", 1)[-1].lower()
    else:
        try:
            resolved = os.path.realpath(os.path.expanduser(text))
        except Exception:
            return None

        for real_path, message in _read_block_messages():
            if resolved == real_path:
                return message
        for prefix, message in _read_block_prefixes():
            if resolved.startswith(prefix):
                return message

        basename = os.path.basename(resolved).lower()
    if basename in BLOCKED_PROJECT_ENV_BASENAMES:
        return f"Blocked: cannot read project-local environment file ({basename}). Use a redacted substitute (e.g. .env.example) when working with config."

    return None
