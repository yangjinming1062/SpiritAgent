import concurrent.futures
import contextlib
import ctypes
import os
import threading
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .config import cfg_get, load_config
from .constants import IS_WINDOWS, get_spiritagent_home

_BLOCKED_PROJECT_ENV_BASENAMES: set[str] = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".env.staging",
    ".envrc",
}
PROFILE_SCOPED_AREAS = ("skills", "plugins", "cron", "memories")
_SANDBOX_BACKEND_DIR = "sandboxes"
_SANDBOX_HOME_SUFFIX = ("home", ".spiritagent")


def validate_within_dir(path: Path, root: Path) -> str | None:
    """若解析结果落在 ``root`` 之外返回错误信息，否则返回 ``None``。"""
    try:
        path.resolve().relative_to(root.resolve())
        return None
    except (ValueError, OSError) as e:
        return f"Path escapes allowed directory: {e}"


def has_traversal_component(path_str: str) -> bool:
    return ".." in Path(path_str).parts


def _spiritagent_home_path() -> Path:
    try:
        return get_spiritagent_home()
    except Exception:
        return Path("~/.spiritagent").expanduser()


_cache_lock = threading.RLock()
_denied_paths_cache: tuple[str, frozenset[str]] | None = None
_denied_prefixes_cache: tuple[str, tuple[str, ...]] | None = None
_denied_prefixes_norm_cache: tuple[str, tuple[str, ...]] | None = None


def build_write_denied_paths(home: str) -> frozenset[str]:
    global _denied_paths_cache
    cached = _denied_paths_cache
    if cached and cached[0] == home:
        return cached[1]
    with _cache_lock:
        if _denied_paths_cache and _denied_paths_cache[0] == home:
            return _denied_paths_cache[1]
        spiritagent, p_home = _spiritagent_home_path(), Path(home)

        def _safe_resolve(p: Path) -> str:
            # ``Path.resolve()`` 在受限 Windows shell 下会卡死, 用裸 ``Thread`` + ``join(timeout)`` 兜底。
            holder: dict[str, str] = {}

            def _runner() -> None:
                with contextlib.suppress(Exception):
                    holder["v"] = str(p.resolve())

            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            t.join(timeout=0.5)
            return holder.get("v") or os.path.normpath(str(p))

        result = frozenset(
            _safe_resolve(p)
            for p in [
                p_home / ".ssh/authorized_keys",
                p_home / ".ssh/id_rsa",
                p_home / ".ssh/id_ed25519",
                p_home / ".ssh/config",
                spiritagent / ".env",
                spiritagent / "anthropic_oauth.json",
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
                "/etc/sudoers",
                "/etc/passwd",
                "/etc/shadow",
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
            "/etc/sudoers.d",
            "/etc/systemd",
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

        def _safe_resolve(p: Path) -> str:
            # ``Path.resolve()`` 在 Windows 上走 ``GetFinalPathNameByHandleW``, 在受限 shell 下会挂死。
            # 用裸 ``Thread`` + ``join(timeout)`` 兜底: ``ThreadPoolExecutor`` 的 ``__exit__`` 默认 ``wait=True``
            # 会让本调用在 ``shutdown`` 上阻塞, 必须用裸 ``Thread``。
            holder: dict[str, str] = {}

            def _runner() -> None:
                with contextlib.suppress(Exception):
                    holder["v"] = str(p.resolve())

            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            t.join(timeout=0.5)
            return holder.get("v") or os.path.normpath(str(p))

        result = tuple(_safe_resolve(p) + os.sep for p in sources if str(p))
        _denied_prefixes_cache = (home, result)
        return result


def _build_normalized_prefixes(home: str) -> tuple[str, ...]:
    """``build_write_denied_prefixes`` 的小写 + 正斜杠预归一版本，供 Windows 上的写检查避免热路径上反复 ``replace().lower()``。"""
    global _denied_prefixes_norm_cache
    cached = _denied_prefixes_norm_cache
    if cached and cached[0] == home:
        return cached[1]
    with _cache_lock:
        if _denied_prefixes_norm_cache and _denied_prefixes_norm_cache[0] == home:
            return _denied_prefixes_norm_cache[1]
        raw = build_write_denied_prefixes(home)
        result = tuple(p.replace("\\", "/").lower() for p in raw)
        _denied_prefixes_norm_cache = (home, result)
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_impl)
            return fut.result(timeout=1.0)
    except (concurrent.futures.TimeoutError, Exception):
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

    try:
        resolved = _get_final_path_by_handle(norm)
    except Exception:
        # 某些受限环境(沙箱 / 受限 shell)对 ``CreateFileW`` + ``GetFinalPathNameByHandleW`` 的调用会卡死或抛错;
        # 退回 ``Path.resolve()`` 让上层仍能拿到合理的规范化结果, 不让单点故障影响整条调用链。
        resolved = None
    if resolved:
        return resolved + stream_suffix

    cur = norm
    tail_parts: list[str] = []
    while cur:
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            break
        tail_parts.insert(0, os.path.basename(cur))
        try:
            resolved_parent = _get_final_path_by_handle(parent)
        except Exception:
            resolved_parent = None
        if resolved_parent:
            joined = os.path.join(resolved_parent, *tail_parts)
            return _strip_device_prefix(joined) + stream_suffix
        cur = parent

    return os.path.realpath(norm) + stream_suffix


def is_write_denied(path: str) -> bool:
    try:
        home = canonicalize_path("~")
        resolved = canonicalize_path(str(path))
    except Exception:
        return True

    base_resolved, stream_suffix = _split_ads_stream(resolved)
    # ``canonicalize_path`` 解析到的实路径可能带 ``\\?\`` 设备前缀（仅对存在路径），不存在的路径回退到 normpath / realpath 时无前缀；
    # 比对时双侧统一剥离, 不让 ``\\?\C:\...`` 与 ``C:\...`` 漏判。
    if IS_WINDOWS:
        resolved = _strip_device_prefix(resolved)
        base_resolved = _strip_device_prefix(base_resolved)
        home = _strip_device_prefix(home)

    # Windows 上路径和前缀匹配都忽略大小写与斜杠方向；两侧统一归一化后，``C:\Windows\System32``、``c:/windows/system32``、``~/.BASHRC`` 等都能命中黑名单。
    resolved_norm = resolved.replace("\\", "/").lower() if IS_WINDOWS else resolved
    base_resolved_norm = base_resolved.replace("\\", "/").lower() if IS_WINDOWS else base_resolved

    denied_paths = build_write_denied_paths(home)
    if IS_WINDOWS:
        denied_paths_lower = {_strip_device_prefix(p).replace("\\", "/").lower() for p in denied_paths}
        if resolved_norm in denied_paths_lower or base_resolved_norm in denied_paths_lower:
            return True
    else:
        if resolved in denied_paths or base_resolved in denied_paths:
            return True

    if IS_WINDOWS:
        normalized_prefixes = _build_normalized_prefixes(home)
        normalized_prefixes_clean = tuple(_strip_device_prefix(p) for p in normalized_prefixes)
        normalized_prefixes_norm = tuple(p.replace("\\", "/").lower() for p in normalized_prefixes_clean)
        if any(resolved_norm.startswith(p) or base_resolved_norm.startswith(p) for p in normalized_prefixes_norm):
            return True
        # 同时阻断目录流的元数据写入（如 ::$INDEX_ALLOCATION）。
        if stream_suffix and any(base_resolved_norm == p.rstrip("/") for p in normalized_prefixes_norm):
            return True
    elif any(resolved.startswith(p) or base_resolved.startswith(p) for p in build_write_denied_prefixes(home)):
        return True

    spiritagent_dirs = []
    with contextlib.suppress(Exception):
        spiritagent_dirs.append(canonicalize_path(str(_spiritagent_home_path())))

    for base_real in spiritagent_dirs:
        base_norm = base_real.replace("\\", "/").lower() if IS_WINDOWS else base_real
        try:
            for n in ("auth.json", "desktop-settings.json", "webhook_subscriptions.json"):
                target_norm = (base_norm + "/" + n).lower() if IS_WINDOWS else os.path.join(base_real, n)
                if (
                    resolved_norm == target_norm
                    or base_resolved_norm == target_norm
                    or resolved == os.path.join(base_real, n)
                    or base_resolved == os.path.join(base_real, n)
                ):
                    return True
            for sub in ("pairing",):
                sub_norm = (base_norm + "/" + sub).lower() if IS_WINDOWS else os.path.join(base_real, sub)
                sub_real = os.path.join(base_real, sub)
                if (
                    resolved_norm == sub_norm
                    or resolved_norm.startswith(sub_norm + "/")
                    or base_resolved_norm == sub_norm
                    or base_resolved_norm.startswith(sub_norm + "/")
                    or resolved == sub_real
                    or resolved.startswith(sub_real + os.sep)
                    or base_resolved == sub_real
                    or base_resolved.startswith(sub_real + os.sep)
                ):
                    return True
        except Exception:
            pass

    return bool(
        (safe_root := _get_safe_write_root())
        and not (
            resolved == safe_root
            or resolved.startswith(safe_root + os.sep)
            or base_resolved == safe_root
            or base_resolved.startswith(safe_root + os.sep)
            or (
                IS_WINDOWS
                and (
                    resolved_norm == safe_root.replace("\\", "/").lower()
                    or resolved_norm.startswith(safe_root.replace("\\", "/").lower() + "/")
                    or base_resolved_norm == safe_root.replace("\\", "/").lower()
                    or base_resolved_norm.startswith(safe_root.replace("\\", "/").lower() + "/")
                )
            )
        ),
    )


def get_read_block_error(path: str) -> str | None:
    try:
        resolved_str = canonicalize_path(str(path))
        resolved = Path(resolved_str)
        base_resolved_str, _ = _split_ads_stream(resolved_str)
        base_resolved = Path(base_resolved_str)
    except Exception:
        return None

    resolved_norm = resolved_str.replace("\\", "/").lower() if IS_WINDOWS else resolved_str
    base_resolved_norm = base_resolved_str.replace("\\", "/").lower() if IS_WINDOWS else base_resolved_str

    spiritagent_dirs = []
    with contextlib.suppress(Exception):
        spiritagent_dirs.append(Path(canonicalize_path(str(_spiritagent_home_path()))))

    for zd in spiritagent_dirs:
        zd_norm = str(zd).replace("\\", "/").lower() if IS_WINDOWS else str(zd)
        for blocked_sub in ("skills/.hub/index-cache", "skills/.hub"):
            blocked_target = zd_norm + "/" + blocked_sub if IS_WINDOWS else str(zd / blocked_sub)
            if (
                resolved_norm == blocked_target
                or resolved_norm.startswith(blocked_target + "/")
                or base_resolved_norm == blocked_target
                or base_resolved_norm.startswith(blocked_target + "/")
            ):
                return f"Access denied: {path} is an internal SpiritAgent cache file. Use skill_view / skills_list instead."

    credential_file_names = (
        "auth.json",
        "auth.lock",
        "anthropic_oauth.json",
        ".env",
        "webhook_subscriptions.json",
        "auth/google_oauth.json",
        "cache/bws_cache.json",
    )
    for zd in spiritagent_dirs:
        zd_norm = str(zd).replace("\\", "/").lower() if IS_WINDOWS else str(zd)
        for name in credential_file_names:
            target_norm = zd_norm + "/" + name if IS_WINDOWS else str((zd / name).resolve())
            if (
                resolved_norm == target_norm
                or base_resolved_norm == target_norm
                or resolved == (zd / name).resolve()
                or base_resolved == (zd / name).resolve()
            ):
                return f"Access denied: {path} is a SpiritAgent credential store and cannot be read directly."

    name_check = resolved.name.lower() if IS_WINDOWS else resolved.name
    base_name_check = base_resolved.name.lower() if IS_WINDOWS else base_resolved.name
    if name_check in _BLOCKED_PROJECT_ENV_BASENAMES or base_name_check in _BLOCKED_PROJECT_ENV_BASENAMES:
        return f"Access denied: {path} is a secret-bearing environment file. Read .env.example instead if checking structure."

    return None


def _resolve_active_profile_name() -> str:
    try:
        spiritagent_real = _spiritagent_home_path().resolve()
        profiles_root = spiritagent_real / "profiles"
        if not profiles_root.is_dir():
            return "default"
        try:
            rel = spiritagent_real.relative_to(profiles_root)
        except ValueError:
            return "default"
        if rel.parts:
            return rel.parts[0]
    except (OSError, RuntimeError):
        pass
    return "default"


@dataclass
class CrossProfileTarget:
    active_profile: str
    target_profile: str
    area: str
    target_path: str


@dataclass
class MirrorTarget:
    target_path: str
    mirror_root: str
    inner_path: str


def classify_cross_profile_target(path: str) -> CrossProfileTarget | None:
    try:
        target = Path(str(path)).expanduser().resolve()
        root_real = _spiritagent_home_path().resolve()
        rel = target.relative_to(root_real)
        parts = rel.parts
        if not parts:
            return None
        if parts[0] in PROFILE_SCOPED_AREAS:
            target_profile, area = "default", parts[0]
        elif parts[0] == "profiles" and len(parts) >= 3 and parts[2] in PROFILE_SCOPED_AREAS:
            target_profile, area = parts[1], parts[2]
        else:
            return None
        if target_profile == (active := _resolve_active_profile_name()):
            return None
        return CrossProfileTarget(
            active_profile=active,
            target_profile=target_profile,
            area=area,
            target_path=str(target),
        )
    except (OSError, RuntimeError, ValueError):
        return None


def get_cross_profile_warning(path: str) -> str | None:
    if info := classify_cross_profile_target(path):
        return f"Cross-profile write blocked: {info.target_path} belongs to profile {info.target_profile!r} (active: {info.active_profile!r}). Confirm with user, and retry with cross_profile=True."
    return None


def _find_sandbox_mirror_segments(parts: tuple) -> int | None:
    for i, part in enumerate(parts):
        if (
            part == _SANDBOX_BACKEND_DIR
            and i + 5 <= len(parts)
            and parts[i + 3] == _SANDBOX_HOME_SUFFIX[0]
            and parts[i + 4] == _SANDBOX_HOME_SUFFIX[1]
        ):
            return i + 4
    return None


def classify_sandbox_mirror_target(path: str) -> MirrorTarget | None:
    try:
        target = Path(str(path)).expanduser().resolve()
        if (idx := _find_sandbox_mirror_segments(target.parts)) is not None:
            return MirrorTarget(
                target_path=str(target),
                mirror_root=str(Path(*target.parts[: idx + 1])),
                inner_path=str(Path(*target.parts[idx + 1 :])) if idx + 1 < len(target.parts) else "",
            )
    except (OSError, RuntimeError):
        pass
    return None


def get_sandbox_mirror_warning(path: str) -> str | None:
    if info := classify_sandbox_mirror_target(path):
        return f"Sandbox-mirror write blocked: {info.target_path} sits under {info.mirror_root!r}. Authoritative file is likely {info.inner_path!r}. Confirm with user, and retry with cross_profile=True."
    return None
