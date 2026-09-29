import contextlib
import ctypes
import functools
import os
import threading
from ctypes import wintypes
from pathlib import Path

from .config import cfg_get, load_config
from .constants import IS_MACOS, IS_WINDOWS, get_spiritagent_home

_BLOCKED_PROJECT_ENV_BASENAMES: set[str] = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".env.staging",
    ".envrc",
}
# $SPIRITAGENT_HOME 下由 Client 维护的文件：desktop-endpoint 含本次启动的 IPC 准入 token，desktop-settings 含终端 SSH 密码，
# desktop-config 决定 Backend 地址。
_HOME_READ_BLOCKED = ("desktop-endpoint.json", "desktop-settings.json")
_HOME_WRITE_DENIED = (*_HOME_READ_BLOCKED, "desktop-config.json")


def validate_within_dir(path: Path, root: Path) -> str | None:
    """若解析结果落在 ``root`` 之外返回错误信息，否则返回 ``None``。"""
    try:
        path.resolve().relative_to(root.resolve())
        return None
    except (ValueError, OSError) as e:
        return f"Path escapes allowed directory: {e}"


def has_traversal_component(path_str: str) -> bool:
    return ".." in Path(path_str).parts


def _resolve_with_timeout(p: Path) -> str:
    """``Path.resolve()`` 在受限 Windows shell 下可能挂死，用 daemon 线程限时，超时退回 normpath。

    不用 ``ThreadPoolExecutor``：其 ``__exit__`` 默认等待工作线程，会一起挂死。
    """
    holder: dict[str, str] = {}

    def _runner() -> None:
        with contextlib.suppress(Exception):
            holder["v"] = str(p.resolve())

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=0.5)
    return holder.get("v") or os.path.normpath(str(p))


_cache_lock = threading.RLock()
_denied_paths_cache: tuple[str, frozenset[str]] | None = None
_denied_prefixes_cache: tuple[str, tuple[str, ...]] | None = None


def build_write_denied_paths(home: str) -> frozenset[str]:
    global _denied_paths_cache
    cached = _denied_paths_cache
    if cached and cached[0] == home:
        return cached[1]
    with _cache_lock:
        if _denied_paths_cache and _denied_paths_cache[0] == home:
            return _denied_paths_cache[1]
        spiritagent, p_home = get_spiritagent_home(), Path(home)
        result = frozenset(
            _resolve_with_timeout(p)
            for p in [
                p_home / ".ssh/authorized_keys",
                p_home / ".ssh/id_rsa",
                p_home / ".ssh/id_ed25519",
                p_home / ".ssh/config",
                *(spiritagent / name for name in _HOME_WRITE_DENIED),
                p_home / ".bashrc",
                p_home / ".zshrc",
                p_home / ".profile",
                p_home / ".bash_profile",
                p_home / ".zprofile",
                p_home / ".netrc",
                p_home / ".pgpass",
                p_home / ".npmrc",
                p_home / ".pypirc",
                p_home / ".git-credentials",
                # macOS 的 /etc 是 /private/etc 的链接，须与目标路径一样经解析后比较。
                Path("/etc/sudoers"),
                Path("/etc/passwd"),
                Path("/etc/shadow"),
            ]
        )
        _denied_paths_cache = (home, result)
        return result


def build_write_denied_prefixes(home: str) -> tuple[str, ...]:
    global _denied_prefixes_cache
    cached = _denied_prefixes_cache
    if cached and cached[0] == home:
        return cached[1]
    with _cache_lock:
        if _denied_prefixes_cache and _denied_prefixes_cache[0] == home:
            return _denied_prefixes_cache[1]
        p_home = Path(home)
        posix_prefixes = [
            p_home / ".ssh",
            p_home / ".aws",
            p_home / ".gnupg",
            p_home / ".kube",
            Path("/etc/sudoers.d"),
            Path("/etc/systemd"),
            p_home / ".docker",
            p_home / ".azure",
            p_home / ".config/gh",
            p_home / ".config/gcloud",
        ]
        windows_prefixes = [
            Path("C:/Windows/System32"),
            Path("C:/Windows/SysWOW64"),
            Path("C:/Windows/WinSxS"),
            Path("C:/Windows/Boot"),
            Path("C:/Windows/Recovery"),
            # WinSxS 体量巨大且结构敏感——禁止在线编辑；Boot/Recovery 存放引导/恢复二进制；System32/SysWOW64 是系统 DLL 主目录。
            Path(os.environ.get("SYSTEMROOT", "C:/Windows")),
            Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")),
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")),
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")),
            p_home / "AppData/Roaming/Microsoft",
            p_home / "AppData/Local/Microsoft",
        ]
        sources = [*posix_prefixes, *windows_prefixes] if IS_WINDOWS else posix_prefixes
        result = tuple(_resolve_with_timeout(p) + os.sep for p in sources)
        _denied_prefixes_cache = (home, result)
        return result


def get_windows_sensitive_prefixes() -> tuple[str, ...]:
    """归一化（小写 + 正斜杠）后的 Windows 系统目录前缀。"""
    rel_entries = (
        "windows/system32/",
        "windows/syswow64/",
        "windows/winsxs/",
        "windows/boot/",
        "windows/recovery/",
        "programdata/",
        "program files/",
        "program files (x86)/",
    )
    drives = _enumerate_windows_drives() or ("c",)
    return tuple(f"{drv}:/{rel}" for drv in drives for rel in rel_entries)


def _enumerate_windows_drives() -> tuple[str, ...]:
    """返回已挂载的 Windows 盘符字母（a-z，小写，无冒号）。"""
    if not IS_WINDOWS:
        return ()
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        drives = tuple(chr(ord("a") + i) for i in range(26) if bitmask & (1 << i))
        return drives or ("c",)
    except Exception:
        return ("c",)


def _get_safe_write_root() -> str | None:
    try:
        root = cfg_get(load_config(), "security", "write_safe_root", default="")
        return str(Path(root).expanduser().resolve()) if root else None
    except Exception:
        return None


if IS_WINDOWS:
    _FILE_READ_ATTRIBUTES = 0x80
    _FILE_SHARE_READ = 1
    _FILE_SHARE_WRITE = 2
    _FILE_SHARE_DELETE = 4
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _VOLUME_NAME_DOS = 0x0
    _FILE_NAME_NORMALIZED = 0x0

    kernel32 = ctypes.windll.kernel32

    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]

    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]

    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _strip_device_prefix(path_str: str) -> str:
    """去除 Windows NT 路径前缀（``\\\\?\\``、``\\\\?\\UNC\\``），但保留 ``\\\\.\\`` 设备路径。"""
    if not path_str:
        return path_str
    norm = path_str.replace("/", "\\")
    norm_upper = norm.upper()
    if norm_upper.startswith("\\\\?\\UNC\\"):
        return "\\\\" + norm[8:]
    if norm_upper.startswith("\\\\?\\"):
        return norm[4:]
    return path_str


def _split_ads_stream(path_str: str) -> tuple[str, str]:
    """把 Windows 路径拆成 (基础路径, NTFS 流后缀)；保留盘符冒号，仅拆分后续组件中的冒号。"""
    if not IS_WINDOWS or not path_str:
        return path_str, ""

    norm = path_str.replace("/", "\\")
    drive = ""
    rest = norm
    if len(norm) >= 2 and norm[0].isalpha() and norm[1] == ":":
        drive = norm[:2]
        rest = norm[2:]

    parts = rest.split("\\")
    if not parts:
        return path_str, ""

    last = parts[-1]
    if ":" in last:
        colon_idx = last.index(":")
        base_last = last[:colon_idx]
        stream_suffix = last[colon_idx:]
        parts[-1] = base_last
        base_path = drive + "\\".join(parts)
        return base_path, stream_suffix

    return path_str, ""


def _get_final_path_by_handle(path_str: str) -> str | None:
    """通过 Win32 GetFinalPathNameByHandleW（动态缓冲）解析权威规范化路径。

    受限 shell / 沙箱下 ``CreateFileW`` 可能挂死(对网络挂载点 / junction 等),
    整路径以工作线程 ``join(timeout=...)`` 兜底: 超时后直接返回 ``None`` 走 ``Path.resolve()`` 退化路径,
    不让单点卡住让上层调用方也跟着死锁。
    """
    if not IS_WINDOWS:
        return None

    def _impl() -> str | None:
        try:
            h = kernel32.CreateFileW(
                path_str,
                _FILE_READ_ATTRIBUTES,
                _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_BACKUP_SEMANTICS,
                None,
            )
            if h == wintypes.HANDLE(-1).value or h == -1:
                return None
            try:
                req_len = kernel32.GetFinalPathNameByHandleW(h, None, 0, _VOLUME_NAME_DOS | _FILE_NAME_NORMALIZED)
                if req_len == 0:
                    return None
                buf = ctypes.create_unicode_buffer(req_len + 1)
                actual = kernel32.GetFinalPathNameByHandleW(
                    h,
                    buf,
                    req_len + 1,
                    _VOLUME_NAME_DOS | _FILE_NAME_NORMALIZED,
                )
                if actual == 0:
                    return None
                return buf.value
            finally:
                with contextlib.suppress(Exception):
                    kernel32.CloseHandle(h)
        except Exception:
            return None

    try:
        # 与 ``_resolve_with_timeout`` 同理用裸 daemon 线程限时，避免挂死在 ``CreateFileW`` 上。
        holder: dict[str, str] = {}

        def _runner() -> None:
            if (v := _impl()) is not None:
                holder["v"] = v

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=1.0)
        return holder.get("v")
    except Exception:
        return None


def canonicalize_path(path: str) -> str:
    """跨平台权威路径规范化（Windows 上处理 NT 设备前缀、ADS、8.3 短名、符号链接/连接点，缺失路径回溯到已存在父目录）。"""
    if not path:
        return ""
    expanded = str(Path(str(path)).expanduser())
    if not IS_WINDOWS:
        return str(Path(expanded).resolve())

    cleaned = _strip_device_prefix(expanded)
    base_path, stream_suffix = _split_ads_stream(cleaned)

    norm = os.path.normpath(base_path)

    if resolved := _get_final_path_by_handle(norm):
        return resolved + stream_suffix

    cur = norm
    tail_parts: list[str] = []
    while cur:
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            break
        tail_parts.insert(0, os.path.basename(cur))
        if resolved_parent := _get_final_path_by_handle(parent):
            joined = os.path.join(resolved_parent, *tail_parts)
            return _strip_device_prefix(joined) + stream_suffix
        cur = parent

    return os.path.realpath(norm) + stream_suffix


def _cmp_key(path: str) -> str:
    """路径比较键：Windows 去设备前缀、统一正斜杠并忽略大小写；macOS 默认文件系统不区分大小写，同样忽略大小写。"""
    if IS_WINDOWS:
        return _strip_device_prefix(path).replace("\\", "/").lower()
    return path.lower() if IS_MACOS else path


def is_write_denied(path: str) -> bool:
    try:
        home = canonicalize_path("~")
        resolved = canonicalize_path(str(path))
    except Exception:
        return True

    base_resolved, stream_suffix = _split_ads_stream(resolved)
    keys = {_cmp_key(resolved), _cmp_key(base_resolved)}
    if keys & {_cmp_key(p) for p in build_write_denied_paths(home)}:
        return True
    prefixes = [_cmp_key(p) for p in build_write_denied_prefixes(home)]
    if any(key.startswith(prefix) for key in keys for prefix in prefixes):
        return True
    # 同时阻断受保护目录自身的流元数据写入（如 ::$INDEX_ALLOCATION）。
    if stream_suffix and any(_cmp_key(base_resolved) == prefix.rstrip("/") for prefix in prefixes):
        return True
    if not (safe_root := _get_safe_write_root()):
        return False
    root_key = _cmp_key(safe_root).rstrip("/")
    return not any(key == root_key or key.startswith(root_key + "/") for key in keys)


@functools.lru_cache(maxsize=4)
def _read_blocked_keys(spiritagent_home: str) -> frozenset[str]:
    return frozenset(_cmp_key(_resolve_with_timeout(Path(spiritagent_home) / name)) for name in _HOME_READ_BLOCKED)


def get_read_block_error(path: str) -> str | None:
    try:
        resolved = canonicalize_path(str(path))
    except Exception:
        return None

    base_resolved, _ = _split_ads_stream(resolved)
    keys = {_cmp_key(resolved), _cmp_key(base_resolved)}
    if keys & _read_blocked_keys(str(get_spiritagent_home())):
        return f"Access denied: {path} holds SpiritAgent connection credentials and cannot be read."
    if any(key.rsplit("/", 1)[-1] in _BLOCKED_PROJECT_ENV_BASENAMES for key in keys):
        return f"Access denied: {path} is a secret-bearing environment file. Read .env.example instead if checking structure."
    return None
