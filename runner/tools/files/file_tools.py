import errno
import functools
import json
import logging
import os
import re
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from envs import (
    active_environments,
    create_environment,
    creation_locks,
    creation_locks_lock,
    env_lock,
    get_env_config,
    last_activity,
    register_env_cleanup_hook,
    resolve_container_task_id,
    start_cleanup_thread,
    task_env_overrides,
)
from utils import (
    IS_WINDOWS,
    cfg_get,
    get_cross_profile_warning,
    get_read_block_error,
    get_sandbox_mirror_warning,
    get_spiritagent_home,
    get_windows_sensitive_prefixes,
    has_traversal_component,
    load_config,
    msys_to_windows_path,
    redact_sensitive_text,
)

from ..registry import registry, tool_error
from .binary_extensions import has_binary_extension
from .helpers import (
    DEFAULT_READ_LIMIT,
    MAX_LINES,
    ShellFileOperations,
    check_stale,
    lock_path,
    normalize_read_pagination,
    normalize_search_pagination,
    note_write,
    record_read,
)
from .native_ops import NativeFileOperations

logger = logging.getLogger(__name__)

_EXPECTED_WRITE_ERRNOS = {errno.EACCES, errno.EPERM, errno.EROFS}

# 我们与 model 无关，无法直接计 token；字符数是稳妥的代理。
# 100K 字符 ≈ 典型分词器下的 25–35K token。单次读取超过该值会构成上下文窗口
# 隐患——模型应使用 offset+limit 读取相关区段。
#
# 可由 Desktop 推送的内存配置覆盖：file_read_max_chars: 200000

_DEFAULT_MAX_READ_CHARS = 100_000
_max_read_chars_cached: int | None = None


def _get_max_read_chars() -> int:
    """返回每次文件读取允许的最大字符数（带进程内缓存）。"""
    global _max_read_chars_cached
    if _max_read_chars_cached is not None:
        return _max_read_chars_cached
    try:
        cfg = load_config()
        val = cfg.get("file_read_max_chars")
        if isinstance(val, int | float) and val > 0:
            _max_read_chars_cached = int(val)
            return _max_read_chars_cached
    except Exception:
        pass
    _max_read_chars_cached = _DEFAULT_MAX_READ_CHARS
    return _max_read_chars_cached


def reset_max_read_chars_cache() -> None:
    """清除 file_read_max_chars 缓存，下次调用重新读配置。"""
    global _max_read_chars_cached
    _max_read_chars_cached = None


# 若文件总大小超过该阈值且调用方未指定窄范围（limit <= 200），提示走精确读取。
_LARGE_FILE_HINT_BYTES = 512_000  # 512 KB

# 设备路径黑名单——读取它们会让进程挂起（无限输出或阻塞输入）。仅按路径判定（不发起 I/O）。

_BLOCKED_DEVICE_PATHS = frozenset(
    {
        # Infinite output — never reach EOF
        "/dev/zero",
        "/dev/random",
        "/dev/urandom",
        "/dev/full",
        "/dev/stdin",
        "/dev/tty",
        "/dev/console",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/fd/0",
        "/dev/fd/1",
        "/dev/fd/2",
    },
)


# 哨兵 ``TERMINAL_CWD`` 值代表「未配置」，不是字面目录基准。过期配置 / .env
# 常留下字面 ``"."``；``auto``/``cwd`` 是设置向导的占位。若把任一当作真实
# 相对基准，会静默把编辑锚到 Agent 进程 cwd（如 worktree 会话却写到主仓库），
# 写入错误的检出点。gateway 在 import 时（gateway/run.py）也消毒同一集合；
# 文件/终端工具层必须同步处理，让 CLI 会话获得同样保护。
# 见 references/worktree-cwd-discipline.md。
_TERMINAL_CWD_SENTINELS = frozenset({"", ".", "./", "auto", "cwd"})


def _configured_terminal_cwd() -> str | None:
    """仅当 ``$TERMINAL_CWD`` 指向真实目录锚点时才返回它。

    哨兵值（见 ``_TERMINAL_CWD_SENTINELS``）与相对路径都被拒绝——相对锚点无法
    独立确定解析基准，正是导致 worktree 编辑误路由的歧义根源。仅接受绝对、
    非哨兵的值。
    """
    raw = (os.environ.get("TERMINAL_CWD") or "").strip()
    # TERMINAL_CWD 是 Desktop 通过 WebSocket 传入的运行时参数，
    # 用于把文件操作锚定到其当前活动的 worktree。
    if raw.lower() in _TERMINAL_CWD_SENTINELS:
        return None
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        return None
    return expanded


def _get_live_tracking_cwd(task_id: str = "default") -> str | None:
    """返回任务实时终端 cwd（如可用），仅用于记账。"""
    try:
        container_key = resolve_container_task_id(task_id)
    except Exception:
        container_key = task_id

    with _file_ops_lock:
        cached = _file_ops_cache.get(container_key) or _file_ops_cache.get(task_id)
    if cached is not None:
        live_cwd = getattr(getattr(cached, "env", None), "cwd", None) or getattr(cached, "cwd", None)
        if live_cwd:
            return live_cwd

    try:
        with env_lock:
            env = active_environments.get(container_key) or active_environments.get(task_id)
            live_cwd = getattr(env, "cwd", None) if env is not None else None
        if live_cwd:
            return live_cwd
    except Exception:
        pass

    return None


def _authoritative_workspace_root(task_id: str = "default") -> str | None:
    """尽力给出绝对工作区根，用于工作树漂移检查。

    优先选实时终端 cwd（Agent 实际所在目录）；终端命令还没跑、live 注册表
    为空时，回退到不含哨兵的绝对 ``$TERMINAL_CWD``。这样 worktree 会话在
    第一次写入之前就能警告/解析到正确 worktree。

    仅当确实没有可靠锚点时才返回 None，调用方此时退回到进程 cwd。
    """
    live = _get_live_tracking_cwd(task_id)
    if live:
        return live
    return _configured_terminal_cwd()


def _resolve_base_dir(task_id: str = "default") -> Path:
    """返回用于解析相对路径的 ABSOLUTE 基目录。

    解析顺序：
      1. 任务实时终端 cwd（Agent 实际工作目录，如 git worktree），有则权威。
      2. 不含哨兵的绝对 ``$TERMINAL_CWD``（``cli.py``/``main.py`` 给 ``-w`` 会话
         设置的 worktree 路径），用于终端注册表尚未填入的早期阶段。
      3. 进程 cwd。

    返回值始终为绝对路径。这是防止 worktree-cwd 漂移的核心不变量：相对或
    哨兵形式的 ``TERMINAL_CWD``（常因过期配置保留字面 ``"."``）无法作为
    解析锚点——放任 ``Path.resolve()`` 会静默解析到 Agent 进程 cwd（如终端
    实际在 worktree，但相对解析却落到主仓库），导致写入错误检出点。因此
    这里直接拒绝哨兵/相对值（而非回退到进程 cwd 锚定），仅作为最终兜底
    才退回到进程 cwd。
    """
    root = _authoritative_workspace_root(task_id)
    base = Path(root).expanduser() if root else Path(os.getcwd())
    if not base.is_absolute():
        # 兜底锚定：实时 cwd 应当已是绝对的；若终端后端意外返回相对 cwd，
        # 在此处一次性挂到进程 cwd 上，结果就不再依赖 resolve() 时的 cwd。
        base = Path(os.getcwd()) / base
    return base.resolve()


@functools.lru_cache(maxsize=1024)
def _resolve_absolute_path(filepath: str) -> Path:
    """缓存的绝对路径解析（与 cwd 无关）。

    仅可传入绝对路径——若传入相对路径，会按调用时的 cwd 解析，并把依赖
    cwd 的结果永久缓存下来。
    """
    filepath = msys_to_windows_path(filepath) if IS_WINDOWS else filepath
    p = Path(filepath).expanduser()
    assert p.is_absolute(), f"_resolve_absolute_path requires an absolute path, got: {filepath!r}"
    return p.resolve()


def _resolve_path_for_task(filepath: str, task_id: str = "default") -> Path:
    """把 *filepath* 解析到任务的绝对基目录下。

    绝对输入路径走 LRU 缓存（与 cwd 无关）；相对路径按实时终端 cwd 解析
    且**不缓存**——中途 ``cd`` 后再走缓存会拿到过期路径。

    在 Windows 上 Agent 传入的路径常为 MSYS 风格（``/c/Users/foo``、
    ``/mnt/c/...``，来自 bash / Git Bash / WSL 透传）。在 ``Path.resolve()``
    之前需先转回原生 ``C:\\...``，否则 ``/c/Users/foo`` 会被当作 runner
    cwd 下的字面路径处理，用户得到莫名其妙的「文件未找到」。
    """
    filepath = msys_to_windows_path(filepath) if IS_WINDOWS else filepath
    p = Path(filepath).expanduser()
    if p.is_absolute():
        return _resolve_absolute_path(filepath)
    return (_resolve_base_dir(task_id) / p).resolve()


def _path_resolution_warning(filepath: str, resolved: Path, task_id: str = "default") -> str | None:
    """当相对路径解析到工作区根之外时给出警告（worktree-cwd 漂移）。

    一旦 Agent 给的相对路径解到了工作区根之外的目录（即即将写入与 Agent
    实际工作目录不同的检出点），立刻返回绝对目标提示。绝对路径、未知根、
    或正常落在工作区根内时返回 None。

    工作区根取实时终端 cwd；未知时退回到不含哨兵的绝对 ``$TERMINAL_CWD``——
    这样终端注册表仍为空（未 ``cd``）的 worktree 会话能在第一次写入时就被警告。
    """
    try:
        if Path(filepath).expanduser().is_absolute():
            return None
        workspace_root = _authoritative_workspace_root(task_id)
        if not workspace_root:
            return None  # No authoritative workspace root to compare against.
        root = Path(workspace_root).expanduser().resolve()
        # Is `resolved` inside `root`?
        try:
            resolved.relative_to(root)
            return None  # Inside the workspace — expected.
        except ValueError:
            return (
                f"Relative path {filepath!r} resolved to {str(resolved)!r}, which is "
                f"OUTSIDE the active workspace ({str(root)!r}). The edit will land in "
                f"a different directory than the terminal's cwd. If this is not "
                f"intended (e.g. a git-worktree session writing into the main "
                f"checkout), pass an absolute path under the workspace instead."
            )
    except Exception:
        return None


def _is_blocked_device_path(path: str) -> str | bool:
    """返回 True 表示该具体设备/fd 路径会卡住读。"""
    normalized = os.path.expanduser(path)
    if normalized in _BLOCKED_DEVICE_PATHS:
        return True
    # /proc/self/fd/0-2 and /proc/<pid>/fd/0-2 are Linux aliases for stdio
    if normalized.startswith("/proc/") and normalized.endswith(("/fd/0", "/fd/1", "/fd/2")):
        return True
    # /proc/*/environ、/proc/*/cmdline、/proc/*/maps 可能泄露宿主进程的
    # 凭据、命令行参数与内存布局。
    return bool(normalized.startswith("/proc/") and normalized.endswith(("/environ", "/cmdline", "/maps")))


def _is_blocked_device(filepath: str) -> bool:
    """若路径会让进程挂起（无限输出或阻塞输入）则返回 True。

    先按字面路径检查，能在解析到终端特定路径之前捕获 ``/dev/stdin`` 等别名；
    再检查解析后的路径，避免工作区里指向 ``/dev/zero`` 的符号链接绕过守卫。
    """
    normalized = os.path.expanduser(filepath)
    if _is_blocked_device_path(normalized):
        return True
    try:
        resolved = os.path.realpath(normalized)
    except (OSError, ValueError):
        return False
    return bool(resolved != normalized and _is_blocked_device_path(resolved))


# 文件工具应拒绝写入、必须经终端工具审批系统的路径。按 ``os.path.realpath``
# 后的前缀匹配。Windows 上匹配大小写不敏感、斜杠不敏感（见 ``_check_sensitive_path``），
# 每项用规范小写 + 正斜杠——保留尾部 ``/`` 是故意的，让 ``C:/Windows`` 不会
# 命中本意仅给 ``C:/Windows/System32`` 的前缀。Windows 条目源自
# ``utils.file_safety.get_windows_sensitive_prefixes``，保证文件工具门禁与
# 终端黑名单同步。
_SENSITIVE_PATH_PREFIXES = (
    # Linux 系统与 systemd 状态
    "/etc/",
    "/boot/",
    "/usr/lib/systemd/",
    "/var/lib/systemd/",
    # macOS 系统只读与本地目录服务（DSLocal 含明文密码哈希、TCC 数据库含隐私授权）
    "/private/etc/",
    "/private/var/",
    "/private/var/db/dslocal/",
    "/system/",
    "/library/apple/usr/libexec/oah/",
    *get_windows_sensitive_prefixes(),
)
# Per-user AppData / NTUSER.DAT——锚到当前登录用户的 home，避免恰好含
# ``appdata/roaming/microsoft/`` 的工作区目录被误拒。无锚定的话，任何
# ``C:\Users\me\Projects\myapp\appdata\...`` 形式的 Windows 工程都会被拒写。
_SENSITIVE_USER_PREFIXES: tuple[str, ...] = ()
_SENSITIVE_USER_EXACTS: tuple[str, ...] = ()
if IS_WINDOWS:
    import os

    def _get_user_home_prefixes() -> tuple[str, ...]:
        homes: set[str] = set()
        # USERPROFILE and HOME are absolute (e.g. C:\Users\alice);
        # HOMEPATH is relative (\Users\alice, no drive letter) and can
        # never match a resolved absolute path, so skip it.
        for var in ("USERPROFILE", "HOME"):
            val = os.environ.get(var)
            if val:
                norm = val.replace("\\", "/").rstrip("/").lower() + "/"
                homes.add(norm)
        return tuple(homes)

    _user_home_prefixes = _get_user_home_prefixes()
    _SENSITIVE_USER_PREFIXES = tuple(
        h + sub for h in _user_home_prefixes for sub in ("appdata/roaming/microsoft/", "appdata/local/microsoft/")
    )
    _SENSITIVE_USER_EXACTS = tuple(
        h + name for h in _user_home_prefixes for name in ("ntuser.dat", "ntuser.dat.log", "ntuser.ini")
    )
elif sys.platform == "darwin":
    # macOS 用户钥匙串（登录密码、WiFi 凭据、证书、Secure Notes）与 TCC 数据库。
    # 与 Windows 的 NTUSER.DAT 同级敏感度——一旦被 agent 读取可提取用户凭据。
    import os

    def _get_user_home_prefixes_macos() -> tuple[str, ...]:
        homes: set[str] = set()
        for var in ("HOME",):
            val = os.environ.get(var)
            if val:
                norm = val.rstrip("/") + "/"
                homes.add(norm.lower())
        return tuple(homes)

    _macos_homes = _get_user_home_prefixes_macos()
    _SENSITIVE_USER_PREFIXES = tuple(
        h + sub
        for h in _macos_homes
        for sub in (
            "library/keychains/",  # 钥匙串数据库（明文 + 已登录凭据）
            "library/application support/com.apple.tcc/",  # 隐私授权数据库
            "library/cookies/",  # 浏览器与系统 cookie
        )
    )
    _SENSITIVE_USER_EXACTS = tuple(h + name for h in _macos_homes for name in ("library/keychains/login.keychain-db",))
_SENSITIVE_EXACT_PATHS = {"/var/run/docker.sock", "/run/docker.sock"}
if IS_WINDOWS:
    _SENSITIVE_EXACT_PATHS |= {
        "c:/windows/system32/config",  # SAM / SYSTEM / SECURITY hives
        "c:/windows/system32/winevt",  # event logs
        "c:/pagefile.sys",
        "c:/hiberfil.sys",
    }

_spiritagent_config_resolved: str | None = None
_spiritagent_config_resolved_loaded = False


def _get_spiritagent_config_resolved() -> str | None:
    """返回 SpiritAgent 设置文件的解析后绝对路径（带缓存）。"""
    global _spiritagent_config_resolved, _spiritagent_config_resolved_loaded
    if _spiritagent_config_resolved_loaded:
        return _spiritagent_config_resolved
    _spiritagent_config_resolved_loaded = True
    try:
        _spiritagent_config_resolved = str((get_spiritagent_home() / "desktop-settings.json").resolve())
    except Exception:
        try:
            _spiritagent_config_resolved = str(Path("~/.spiritagent/desktop-settings.json").expanduser().resolve())
        except Exception:
            _spiritagent_config_resolved = None
    return _spiritagent_config_resolved


def _check_sensitive_path(filepath: str, task_id: str = "default") -> str | None:
    """若路径指向敏感系统位置则返回错误信息。"""
    try:
        resolved = str(_resolve_path_for_task(filepath, task_id))
    except (OSError, ValueError):
        resolved = filepath
    # Apply the same MSYS translation that _resolve_path_for_task uses so
    # the fallback path also matches the canonical prefix list on Windows.
    # 与 _resolve_path_for_task 同样应用 MSYS 翻译，使 fallback 路径也能
    # 在 Windows 上匹配规范前缀表。
    norm_input = msys_to_windows_path(filepath) if IS_WINDOWS else filepath
    normalized = os.path.normpath(os.path.expanduser(norm_input))
    # Windows 下 Agent 传入路径形式各异（`C:\Windows\System32`、`c:/windows/system32/`、
    # `C:/Windows/System32\\foo`），需统一与规范的小写 + 正斜杠前缀表比对。
    if IS_WINDOWS:
        resolved = resolved.replace("\\", "/").lower()
        normalized = normalized.replace("\\", "/").lower()
    _err = (
        f"Refusing to write to sensitive system path: {filepath}\n"
        "Use the terminal tool with sudo if you need to modify system files."
    )
    for prefix in _SENSITIVE_PATH_PREFIXES:
        if resolved.startswith(prefix) or normalized.startswith(prefix):
            return _err
    if IS_WINDOWS:
        for prefix in _SENSITIVE_USER_PREFIXES:
            if resolved.startswith(prefix) or normalized.startswith(prefix):
                return _err
        if resolved in _SENSITIVE_USER_EXACTS or normalized in _SENSITIVE_USER_EXACTS:
            return _err
    if resolved in _SENSITIVE_EXACT_PATHS or normalized in _SENSITIVE_EXACT_PATHS:
        return _err
    # Prevent agents from modifying the SpiritAgent settings file directly.
    # Security-sensitive configuration lives here; a malicious or
    # prompt-injected agent could silently disable exec approval by writing to
    # this file.
    spiritagent_config = _get_spiritagent_config_resolved()
    if spiritagent_config and (resolved == spiritagent_config or normalized == spiritagent_config):
        return (
            f"Refusing to write to SpiritAgent settings file: {filepath}\n"
            "Agent cannot modify security-sensitive configuration. "
            "Change settings via the Desktop settings page instead."
        )
    return None


def _check_cross_profile_path(filepath: str, task_id: str = "default") -> str | None:
    """软守卫：``filepath`` 落到另一个 SpiritAgent profile 作用域时返回警告字符串。

    写入本身不阻断——调用方应把警告附在 result 而非拒绝执行。
    实际硬阻断（sandbox-mirror / container-mirror）见 ``_check_hard_path_blocks``。
    """
    try:
        resolved = str(_resolve_path_for_task(filepath, task_id))
    except (OSError, ValueError):
        resolved = filepath
    return get_cross_profile_warning(resolved)


def _check_hard_path_blocks(filepath: str, task_id: str = "default") -> str | None:
    """硬阻断：sandbox-mirror 写入等于无效，必须拒。

    这不是「纵深防御」而是真实错误（写入不会生效）。
    """
    try:
        resolved = str(_resolve_path_for_task(filepath, task_id))
    except (OSError, ValueError):
        resolved = filepath
    return get_sandbox_mirror_warning(resolved)


_CROSS_PROFILE_AUDIT_LOG = get_spiritagent_home() / "cross-profile-audit.log"


def _audit_cross_profile_bypass(task_id: str, path: str, op: str) -> None:
    """记录跨 profile 绕过事件，便于审计与告警。"""
    try:
        _CROSS_PROFILE_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _CROSS_PROFILE_AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "task_id": task_id,
                        "path": path,
                        "op": op,
                        "active_profile": cfg_get(load_config(), "spiritagent", "profile", default="default"),
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
    except Exception as e:
        logger.debug("cross-profile audit log failed: %s", e)


def _is_expected_write_exception(exc: Exception) -> bool:
    """返回 True 表示是可预期的写入拒绝（不应记入 error 日志）。"""
    if isinstance(exc, PermissionError):
        return True
    return bool(isinstance(exc, OSError) and exc.errno in _EXPECTED_WRITE_ERRNOS)


_file_ops_lock = threading.Lock()
_file_ops_cache: dict = {}

# 记录每个任务的读取，用于检测重读循环与去重。每个 task_id 存储：
#   "last_key":     最近一次 read/search 调用的 key（或 None）
#   "consecutive":  完全相同的调用连续重复的次数
#   "read_history": (path, offset, limit) 元组集合，供 get_read_files_summary 使用
#   "dedup":        (resolved_path, offset, limit) → mtime 浮点 的字典，
#                   用于跳过未变化文件的重读。上下文压缩时重置（原内容被
#                   摘要化掉，模型需要再拿到完整内容）。
#   "read_timestamps": resolved_path → mtime 浮点的字典，记录本任务最近
#                      一次读/写该文件的时间。write_file 与 patch 据此检测
#                      Agent 读后发生的外部修改。
#
#                   这样同任务的连续编辑不会触发误报陈旧警告。
_read_tracker_lock = threading.Lock()
_read_tracker: dict = {}

# 跟踪每个 (task_id, resolved_path) 的连续补丁失败。模型反复对同一文件
# 打补丁失败时用于逐步增强提示（典型原因：文件视图过期、old_string 歧义、
# 或 Agent 读后到打补丁之间文件被外部修改）。同一路径打补丁成功后重置。
_patch_failure_lock = threading.Lock()
_patch_failure_tracker: dict = {}  # {task_id: {resolved_path: count}}


def _record_patch_failure(task_id: str, resolved_path: str) -> int:
    """递增并返回该路径的连续失败计数。"""
    with _patch_failure_lock:
        task_failures = _patch_failure_tracker.setdefault(task_id, {})
        # Cap dict size per task to avoid unbounded growth in long sessions
        # where the agent fails on many distinct files.  64 distinct
        # failing files per task is generous; older entries get evicted.
        if len(task_failures) >= 64 and resolved_path not in task_failures:
            try:
                first_key = next(iter(task_failures))
                del task_failures[first_key]
            except StopIteration:
                pass
        task_failures[resolved_path] = task_failures.get(resolved_path, 0) + 1
        return task_failures[resolved_path]


def _reset_patch_failures(task_id: str, resolved_paths: list) -> None:
    """清除给定路径的连续失败计数。"""
    if not resolved_paths:
        return
    with _patch_failure_lock:
        task_failures = _patch_failure_tracker.get(task_id)
        if not task_failures:
            return
        for rp in resolved_paths:
            task_failures.pop(rp, None)


# 每个任务在 _read_tracker[task_id] 内各容器的上限。CLI 会话全程使用同一
# task_id；无这些上限的话，1 万次读取的会话会累积 ~1.5MB 的 dict/set 状态，
# 而其中大部分后续不再被引用（只有最近几次读取用于去重、循环检测与外部
# 编辑警告）。硬上限把累积量控制在几百 KB，与会话长度无关。
_READ_HISTORY_CAP = 500  # set; used only by get_read_files_summary
_DEDUP_CAP = 1000  # dict; skip-identical-reread guard
_READ_TIMESTAMPS_CAP = 1000  # dict; external-edit detection for write/patch
_READ_DEDUP_STATUS_MESSAGE = (
    "File unchanged since last read. The content from the earlier read_file result in this "
    "conversation is still current — refer to that instead of re-reading."
)


def _cap_read_tracker_data(task_data: dict) -> None:
    """对每个任务的 read-tracker 子容器强制大小上限。

    调用方必须持有 ``_read_tracker_lock``。驱逐策略：
      * ``read_history``（set）：溢出时任意弹出。集合仅供诊断摘要，丢老的
        条目只是裁掉摘要的尾部，可接受。
      * ``dedup`` / ``read_timestamps``（dict）：按插入顺序弹出最旧（Python 3.7+
        dict 保证有序）。被驱逐条目在下次重读时失去 dedup 跳过（文件会被
        重发一次），以及失去外部编辑 mtime 比对（write/patch 退回到非
        mtime 检查）。两种都是优雅降级，不是 bug。
    """
    rh = task_data.get("read_history")
    if rh is not None and len(rh) > _READ_HISTORY_CAP:
        excess = len(rh) - _READ_HISTORY_CAP
        for _ in range(excess):
            try:
                rh.pop()
            except KeyError:
                break

    dedup = task_data.get("dedup")
    if dedup is not None and len(dedup) > _DEDUP_CAP:
        excess = len(dedup) - _DEDUP_CAP
        for _ in range(excess):
            try:
                dedup.pop(next(iter(dedup)))
            except (StopIteration, KeyError):
                break

    dedup_hits = task_data.get("dedup_hits")
    if dedup_hits is not None and len(dedup_hits) > _DEDUP_CAP:
        excess = len(dedup_hits) - _DEDUP_CAP
        for _ in range(excess):
            try:
                dedup_hits.pop(next(iter(dedup_hits)))
            except (StopIteration, KeyError):
                break

    ts = task_data.get("read_timestamps")
    if ts is not None and len(ts) > _READ_TIMESTAMPS_CAP:
        excess = len(ts) - _READ_TIMESTAMPS_CAP
        for _ in range(excess):
            try:
                ts.pop(next(iter(ts)))
            except (StopIteration, KeyError):
                break


def _is_internal_file_status_text(content: str) -> bool:
    """若内容看起来像内部文件工具状态（而非真实文件字节）则返回 True。

    read_file dedup 状态消息绝不能被当成文件内容持久化。最显见的形态是
    模型原样回显这条消息，但实际中调用方常会先包一层短文本（前导 "Note:"、
    尾部换行 + 短评注等）再调 write_file。把任何「较短、内容由状态消息主导」
    的写入都视为同类污染。

    启发式：
      * 严格相等（strip 后）—— 字面形态。
      * 或 strip 后的内容包含整条状态消息，且总长度 ≤ 2 倍消息长度。短而
        状态主导的写入不可能是真实文件——合法文档/笔记即便引用了这条内部
        消息也通常远长于此。
    """
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    if not stripped:
        return False
    if stripped == _READ_DEDUP_STATUS_MESSAGE:
        return True
    return bool(_READ_DEDUP_STATUS_MESSAGE in stripped and len(stripped) <= 2 * len(_READ_DEDUP_STATUS_MESSAGE))


def _get_file_ops(task_id: str = "default") -> ShellFileOperations | NativeFileOperations:
    """获取或创建任务对应的文件操作实例。

    遵守 ``TERMINAL_ENV`` 配置：若任务尚未有环境，按配置后端（local、ssh 等）新建，
    而非默认 local。

    线程安全：复用 terminal_tool 的 per-task 创建锁，防止并发调用重复创建沙箱。

    注：子 Agent 的 task_id 通过 ``resolve_container_task_id`` 折叠为
    "default"，使 delegate_task 子任务共享父任务容器与缓存的 file_ops。
    持有 env override 的 RL/benchmark task_id 保持隔离。
    """

    task_id = resolve_container_task_id(task_id)

    # 快速路径：检查缓存——并校验底层环境仍存活（可能已被清理线程杀掉）。
    with _file_ops_lock:
        cached = _file_ops_cache.get(task_id)
    if cached is not None:
        with env_lock:
            if task_id in active_environments:
                last_activity[task_id] = time.time()
                return cached
            else:
                # Environment was cleaned up -- invalidate stale cache entry
                with _file_ops_lock:
                    _file_ops_cache.pop(task_id, None)

    # 需要先确保环境存在，再构建 file_ops。

    with creation_locks_lock:
        if task_id not in creation_locks:
            creation_locks[task_id] = threading.Lock()
        task_lock = creation_locks[task_id]

    with task_lock:
        # Double-check: another thread may have created it while we waited
        with env_lock:
            if task_id in active_environments:
                last_activity[task_id] = time.time()
                terminal_env = active_environments[task_id]
            else:
                terminal_env = None

        if terminal_env is None:
            config = get_env_config()
            env_type = config["env_type"]
            overrides = task_env_overrides.get(task_id, {})

            cwd = overrides.get("cwd") or config["cwd"]
            logger.info("Creating new %s environment for task %s...", env_type, task_id[:8])

            ssh_config = None
            if env_type == "ssh":
                ssh_config = {
                    "host": config.get("ssh_host", ""),
                    "user": config.get("ssh_user", ""),
                    "port": config.get("ssh_port", 22),
                    "key": config.get("ssh_key", ""),
                    "password": config.get("ssh_password", ""),
                    "persistent": config.get("ssh_persistent", False),
                }

            local_config = None
            if env_type == "local":
                local_config = {"persistent": config.get("local_persistent", False)}

            terminal_env = create_environment(
                env_type=env_type,
                cwd=cwd,
                timeout=config["timeout"],
                ssh_config=ssh_config,
                local_config=local_config,
                task_id=task_id,
            )

            with env_lock:
                active_environments[task_id] = terminal_env
                last_activity[task_id] = time.time()

            start_cleanup_thread()
            logger.info("%s environment ready for task %s", env_type, task_id[:8])

    file_ops = (
        NativeFileOperations(cwd=getattr(terminal_env, "cwd", None))
        if getattr(terminal_env, "env_type", None) == "local"
        else ShellFileOperations(terminal_env)
    )

    with _file_ops_lock:
        _file_ops_cache[task_id] = file_ops
    return file_ops


def clear_file_ops_cache(task_id: str | None = None) -> None:
    """清空文件操作缓存。"""
    with _file_ops_lock:
        if task_id:
            _file_ops_cache.pop(task_id, None)
        else:
            _file_ops_cache.clear()
    # 绝对路径解析是按路径作用域（而非按任务），缓存可在任务切换间存活。


register_env_cleanup_hook(clear_file_ops_cache)


# 若 worktree 切换确实改变了符号链接，需显式调用 _resolve_absolute_path.cache_clear()。


def list_directory_tool(path: str, task_id: str = "default", cancel_token: Any = None) -> str:
    """列出目录内容。"""
    if not path:
        return json.dumps({"error": "list_directory: missing 'path'."})
    try:
        if _is_blocked_device(path):
            return json.dumps({"error": f"Cannot read '{path}': device file blocked."})

        _resolved = _resolve_path_for_task(path, task_id)

        block_error = get_read_block_error(str(_resolved))
        if block_error:
            return json.dumps({"error": block_error})

        if not _resolved.exists():
            return json.dumps({"error": f"Directory '{path}' not found."})

        if not _resolved.is_dir():
            return json.dumps({"error": f"Path '{path}' is not a directory."})

        entries = []
        for p in _resolved.iterdir():
            stat = p.stat()
            entries.append(
                {
                    "name": p.name + ("/" if p.is_dir() else ""),
                    "is_dir": p.is_dir(),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                },
            )
        entries.sort(key=lambda x: (not x["is_dir"], x["name"]))
        # 结果大小截断: ``iterdir`` 上限按 ``registry.get_max_result_size()`` 估计 (每条 ~120 字符)
        # 提前截断避免一次列 10K+ 条目时撑爆模型上下文。
        max_entries = max(50, registry.get_max_result_size() // 120)
        truncated = len(entries) > max_entries
        if truncated:
            entries = entries[:max_entries]
        result: dict[str, Any] = {"path": str(_resolved), "entries": entries}
        if truncated:
            result["truncated"] = True
            result["hint"] = f"Directory has more than {max_entries} entries; use search_files to filter."
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def read_file_tool(
    path: str,
    offset: int = 1,
    limit: int = 500,
    task_id: str = "default",
    cancel_token: Any = None,
) -> str:
    """带分页与行号读取文件。"""
    if not path:
        return json.dumps({"error": "read_file: missing 'path'."})
    try:
        offset, limit = normalize_read_pagination(offset, limit)

        # ── Device path guard ─────────────────────────────────────────

        # blocking on input).  Pure path check — no I/O.
        if _is_blocked_device(path):
            return json.dumps(
                {
                    "error": (
                        f"Cannot read '{path}': this is a device file that would block or produce infinite output."
                    ),
                },
            )

        _resolved = _resolve_path_for_task(path, task_id)

        # ── Binary file guard ─────────────────────────────────────────

        if has_binary_extension(str(_resolved)):
            _ext = _resolved.suffix.lower()
            return json.dumps(
                {
                    "error": (
                        f"Cannot read binary file '{path}' ({_ext}). "
                        "Use vision_analyze for images, or terminal to inspect binary files."
                    ),
                },
            )

        # ── SpiritAgent internal path guard ────────────────────────────────
        # Prevent prompt injection via catalog or hub metadata files,
        # and block credential stores under SPIRITAGENT_HOME.  Pass the
        # already-resolved path so a relative-path read against
        # TERMINAL_CWD == SPIRITAGENT_HOME (e.g. "auth.json") still hits the
        # denylist — get_read_block_error's own resolve() runs against
        # the Python process cwd, which can differ.
        block_error = get_read_block_error(str(_resolved))
        if block_error:
            return json.dumps({"error": block_error})

        # ── Dedup check ───────────────────────────────────────────────
        # If we already read this exact (path, offset, limit) and the
        # file hasn't been modified since, return a lightweight stub
        # instead of re-sending the same content.  Saves context tokens.
        resolved_str = str(_resolved)
        dedup_key = (resolved_str, offset, limit)
        with _read_tracker_lock:
            task_data = _read_tracker.setdefault(
                task_id,
                {
                    "last_key": None,
                    "consecutive": 0,
                    "read_history": set(),
                    "dedup": {},
                    "dedup_hits": {},
                    "read_timestamps": {},
                },
            )
            cached_mtime = task_data.get("dedup", {}).get(dedup_key)

        if cached_mtime is not None:
            try:
                current_mtime = os.path.getmtime(resolved_str)
                if current_mtime == cached_mtime:
                    # Count repeated stub returns so weak tool-followers that
                    # ignore the "refer to earlier result" hint don't burn
                    # their iteration budget in an infinite read loop.  After
                    # 2 stubs for the same key we escalate to a hard block
                    # mirroring the count>=4 path on real reads.
                    with _read_tracker_lock:
                        hits = task_data["dedup_hits"].get(dedup_key, 0) + 1
                        task_data["dedup_hits"][dedup_key] = hits
                        _cap_read_tracker_data(task_data)

                    if hits >= 2:
                        return json.dumps(
                            {
                                "error": (
                                    f"BLOCKED: You have called read_file on this "
                                    f"exact region {hits + 1} times and the file "
                                    "has NOT changed. STOP calling read_file for "
                                    "this path — the content from your earlier "
                                    "read_file result in this conversation is "
                                    "still current. Proceed with your task using "
                                    "the information you already have."
                                ),
                                "path": path,
                                "already_read": hits + 1,
                            },
                            ensure_ascii=False,
                        )

                    return json.dumps(
                        {
                            "status": "unchanged",
                            "message": _READ_DEDUP_STATUS_MESSAGE,
                            "path": path,
                            "dedup": True,
                            "content_returned": False,
                        },
                        ensure_ascii=False,
                    )
            except OSError:
                pass  # stat failed — fall through to full read

        # ── Perform the read ──────────────────────────────────────────
        file_ops = _get_file_ops(task_id)
        result = file_ops.read_file(path, offset, limit)
        result_dict = result.to_dict()

        # ── Character-count guard ─────────────────────────────────────
        # We're model-agnostic so we can't count tokens; characters are
        # the best proxy we have.  If the read produced an unreasonable
        # amount of content, reject it and tell the model to narrow down.
        # Note: we check the formatted content (with line-number prefixes),
        # not the raw file size, because that's what actually enters context.

        content_len = len(result.content or "")
        file_size = result_dict.get("file_size", 0)
        max_chars = _get_max_read_chars()
        if content_len > max_chars:
            total_lines = result_dict.get("total_lines", "unknown")
            return json.dumps(
                {
                    "error": (
                        f"Read produced {content_len:,} characters which exceeds "
                        f"the safety limit ({max_chars:,} chars). "
                        "Use offset and limit to read a smaller range. "
                        f"The file has {total_lines} lines total."
                    ),
                    "path": path,
                    "total_lines": total_lines,
                    "file_size": file_size,
                },
                ensure_ascii=False,
            )

        # ── Redact secrets (after guard check to skip oversized content) ──
        if result.content:
            result.content = redact_sensitive_text(result.content)
            result_dict["content"] = result.content

        # Large-file hint: if the file is big and the caller didn't ask
        # for a narrow window, nudge toward targeted reads.
        if file_size and file_size > _LARGE_FILE_HINT_BYTES and limit > 200 and result_dict.get("truncated"):
            result_dict.setdefault(
                "_hint",
                (
                    f"This file is large ({file_size:,} bytes). Consider reading only the "
                    "section you need with offset and limit to keep context usage efficient."
                ),
            )

        # ── Track for consecutive-loop detection ──────────────────────
        read_key = ("read", path, offset, limit)
        with _read_tracker_lock:
            # Real read succeeded — this key is no longer in a stub-loop, so
            # reset its hit counter.  (File either changed or stat failed
            # earlier and we fell through.)
            task_data["dedup_hits"].pop(dedup_key, None)
            task_data["read_history"].add((path, offset, limit))
            if task_data["last_key"] == read_key:
                task_data["consecutive"] += 1
            else:
                task_data["last_key"] = read_key
                task_data["consecutive"] = 1
            count = task_data["consecutive"]

            # Store mtime at read time for two purposes:
            # 1. Dedup: skip identical re-reads of unchanged files.
            # 2. Staleness: warn on write/patch if the file changed since
            #    the agent last read it (external edit, concurrent agent, etc.).
            try:
                _mtime_now = os.path.getmtime(resolved_str)
                task_data["dedup"][dedup_key] = _mtime_now
                task_data.setdefault("read_timestamps", {})[resolved_str] = _mtime_now
            except OSError:
                pass  # Can't stat — skip tracking for this entry

            # Bound the per-task containers so a long CLI session doesn't
            # accumulate megabytes of dict/set state.  See _cap_read_tracker_data.
            _cap_read_tracker_data(task_data)

        # Cross-agent file-state registry (separate from per-task read
        # tracker above): records that THIS agent has read this path so
        # write/patch can detect sibling-subagent writes that happened
        # after our read.  Partial read when offset>1 or the read was
        # truncated (large file with more content than limit covered).
        # Outside the _read_tracker_lock so the registry's own locking
        # isn't nested under ours.
        try:
            _partial = (offset > 1) or bool(result_dict.get("truncated"))
            record_read(task_id, resolved_str, partial=_partial)
        except Exception:
            logger.debug("record_read failed", exc_info=True)

        if count >= 4:
            # Hard block: stop returning content to break the loop
            return json.dumps(
                {
                    "error": (
                        f"BLOCKED: You have read this exact file region {count} times in a row. "
                        "The content has NOT changed. You already have this information. "
                        "STOP re-reading and proceed with your task."
                    ),
                    "path": path,
                    "already_read": count,
                },
                ensure_ascii=False,
            )
        elif count >= 3:
            result_dict["_warning"] = (
                f"You have read this exact file region {count} times consecutively. "
                "The content has not changed since your last read. Use the information you already have. "
                "If you are stuck in a loop, stop reading and proceed with writing or responding."
            )

        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))


def _invalidate_dedup_for_path(filepath: str, task_id: str) -> None:
    """移除 dedup 缓存中所有已解析路径匹配 *filepath* 的条目。

    write_file 和 patch 之后调用，保证对同一路径的随后 read_file 总是返回
    新内容，而非过期的「文件未变」占位。dedup 缓存键是
    ``(resolved_path, offset, limit)``，需要清掉该路径所有 offset/limit
    组合——任一缓存范围都可能已失效。

    调用方不得持有 ``_read_tracker_lock``（本函数内部自行加锁）。
    """
    try:
        resolved = str(_resolve_path_for_task(filepath, task_id))
    except (OSError, ValueError):
        return
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id)
        if task_data is None:
            return
        dedup = task_data.get("dedup")
        if not dedup:
            return

        stale_keys = [k for k in dedup if k[0] == resolved]
        for k in stale_keys:
            del dedup[k]


def _update_read_timestamp(filepath: str, task_id: str) -> None:
    """写入成功后刷新该文件的修改时间戳。

    write_file 和 patch 之后调用：同任务的连续编辑会用新时间戳覆盖旧值，
    避免误报陈旧警告。同时清空该路径的 dedup 缓存，让随后的 read_file
    拿到新内容（修复 #13144）。
    """
    # 先清 dedup（再进入锁更新时间戳）
    _invalidate_dedup_for_path(filepath, task_id)
    try:
        resolved = str(_resolve_path_for_task(filepath, task_id))
        current_mtime = os.path.getmtime(resolved)
    except (OSError, ValueError):
        return
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id)
        if task_data is not None:
            task_data.setdefault("read_timestamps", {})[resolved] = current_mtime
            _cap_read_tracker_data(task_data)


def _check_file_staleness(filepath: str, task_id: str) -> str | None:
    """检查文件是否在 Agent 上次读取后被修改过。

    若自本任务上次 read_file 以来 mtime 变化则返回警告字符串；文件是新的
    或从未被读取则返回 None。不阻断写入，写入仍会继续。
    """
    try:
        resolved = str(_resolve_path_for_task(filepath, task_id))
    except (OSError, ValueError):
        return None
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id)
        if not task_data:
            return None
        read_mtime = task_data.get("read_timestamps", {}).get(resolved)
    if read_mtime is None:
        return None  # File was never read — nothing to compare against
    try:
        current_mtime = os.path.getmtime(resolved)
    except OSError:
        return None  # Can't stat — file may have been deleted, let write handle it
    if current_mtime != read_mtime:
        return (
            f"Warning: {filepath} was modified since you last read it "
            "(external edit or concurrent agent). The content you read may be "
            "stale. Consider re-reading the file to verify before writing."
        )
    return None


def write_file_tool(
    path: str,
    content: str,
    task_id: str = "default",
    cross_profile: bool = False,
    cancel_token: Any = None,
) -> str:
    """把内容写入文件。

    ``cross_profile`` 用于绕过跨 SpiritAgent profile 软守卫。该守卫只在
    写入落在其他 profile 的 skills/plugins/cron/memories 目录时触发，其他
    路径不受影响。需用户在明确指示后传 ``True``——与 terminal 工具的
    ``force`` 同形。
    """
    sensitive_err = _check_sensitive_path(path, task_id)
    if sensitive_err:
        return tool_error(sensitive_err)
    # sandbox-mirror / container-mirror 写入无效，必须硬阻断。
    hard_block = _check_hard_path_blocks(path, task_id)
    if hard_block:
        return tool_error(hard_block)
    cross_profile_warning = _check_cross_profile_path(path, task_id)
    if cross_profile and cross_profile_warning:
        # 用户显式 cross_profile=True：写成功 + 留审计记录。
        _audit_cross_profile_bypass(task_id, path, "write_file")
    if _is_internal_file_status_text(content):
        return tool_error(
            "Refusing to write internal read_file status text as file content. "
            "Re-read the file or reconstruct the intended file contents before writing.",
        )
    try:
        _resolved = str(_resolve_path_for_task(path, task_id))

        # Serialize the read→modify→write region per-path so concurrent
        # subagents can't interleave on the same file.  Different paths
        # remain fully parallel.
        with lock_path(_resolved):
            # Cross-agent staleness wins over per-task warning when both
            # fire — its message names the sibling subagent.
            sibling_warning = check_stale(task_id, _resolved)
            stale_warning = _check_file_staleness(path, task_id)
            # Workspace-divergence warning: relative path resolving outside the
            # terminal's cwd (the worktree-cwd bug). Lowest priority of the three.
            cwd_warning = _path_resolution_warning(path, Path(_resolved), task_id)
            file_ops = _get_file_ops(task_id)
            result = file_ops.write_file(_resolved, content)
            result_dict = result.to_dict()
            # 软守卫 cross-profile 警告拼接到最前；其余三个按触发顺序展示。
            # 用 `|` 拼接而非 `or`：多个警告并存时模型能一次看到全部，而非只看首个。
            all_warnings = [w for w in (cross_profile_warning, sibling_warning, stale_warning, cwd_warning) if w]
            if all_warnings:
                result_dict["_warning"] = " | ".join(all_warnings)
            # Always report the ABSOLUTE path actually written, so a wrong-cwd

            # the edit to the wrong checkout.
            result_dict["resolved_path"] = _resolved
            if not result_dict.get("error"):
                result_dict["files_modified"] = [_resolved]

            # writes by this task don't trigger false staleness warnings.
            _update_read_timestamp(path, task_id)
            if not result_dict.get("error"):
                note_write(task_id, _resolved)
        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        if _is_expected_write_exception(e):
            logger.debug("write_file expected denial: %s: %s", type(e).__name__, e)
        else:
            logger.error("write_file error: %s: %s", type(e).__name__, e, exc_info=True)
        return tool_error(str(e))


def patch_tool(
    mode: str = "replace",
    path: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    replace_all: bool = False,
    patch: str | None = None,
    task_id: str = "default",
    cross_profile: bool = False,
    cancel_token: Any = None,
) -> str:
    """以 replace 模式或 V4A 补丁格式修改文件。

    ``cross_profile`` 用于绕过跨 SpiritAgent profile 软守卫，作用于其他
    profile 的 skills/plugins/cron/memories 目标。形式与 ``write_file`` 一致。
    """
    # replace 模式（显式 path）与 V4A 模式（从 patch 抽取）都需敏感路径检查
    _paths_to_check = []
    if path:
        _paths_to_check.append(path)
    if mode == "patch" and patch:
        for _m in re.finditer(r"^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+)$", patch, re.MULTILINE):
            v4a_path = _m.group(1).strip()
            # V4A path headers come from patch CONTENT, not the explicit
            # ``path=`` arg — so they're more attacker-influenceable (skill
            # content, web extract, prompt injection). Reject ``..`` traversal
            # in V4A headers: a legitimate multi-file patch from a single cwd
            # can always emit absolute paths or paths relative to the agent's
            # cwd without ``..``. The explicit ``path=`` arg is unchanged
            # because the agent uses relative ``..`` paths legitimately
            # (e.g. ``patch path="../other_module/x.py"`` from a worktree).
            if has_traversal_component(v4a_path):
                return tool_error(
                    f"V4A patch header contains '..' traversal: {v4a_path!r}. "
                    "Use the agent's cwd-relative path (no '..') or an absolute "
                    "path in '*** Update File:' / '*** Add File:' / '*** Delete File:' headers.",
                )
            _paths_to_check.append(v4a_path)
    cross_profile_warnings: list[str] = []
    for _p in _paths_to_check:
        sensitive_err = _check_sensitive_path(_p, task_id)
        if sensitive_err:
            return tool_error(sensitive_err)
        hard_block = _check_hard_path_blocks(_p, task_id)
        if hard_block:
            return tool_error(hard_block)
        cp_warn = _check_cross_profile_path(_p, task_id)
        if cp_warn:
            cross_profile_warnings.append(cp_warn)
            if cross_profile:
                _audit_cross_profile_bypass(task_id, _p, "patch")
    try:
        # Resolve paths for locking.  Ordered + deduplicated so concurrent
        # callers lock in the same order — prevents deadlock on overlapping
        # multi-file V4A patches.
        _resolved_paths: list[str] = []
        _seen: set[str] = set()
        for _p in _paths_to_check:
            try:
                _r = str(_resolve_path_for_task(_p, task_id))
            except Exception:
                _r = None
            if _r and _r not in _seen:
                _resolved_paths.append(_r)
                _seen.add(_r)
        _resolved_paths.sort()

        # path this degenerates to one lock; on empty list (unresolvable)
        # it's a no-op and execution falls through unchanged.

        with ExitStack() as _locks:
            for _r in _resolved_paths:
                _locks.enter_context(lock_path(_r))

            # then per-task tracker as a fallback.
            stale_warnings: list[str] = []
            _path_to_resolved: dict[str, str] = {}
            for _p in _paths_to_check:
                try:
                    _r = str(_resolve_path_for_task(_p, task_id))
                except Exception:
                    _r = None
                _path_to_resolved[_p] = _r
                _cross = check_stale(task_id, _r) if _r else None
                _sw = _cross or _check_file_staleness(_p, task_id)
                if not _sw and _r:
                    # Workspace-divergence warning (worktree-cwd bug): relative
                    # path resolving outside the terminal's cwd.
                    _sw = _path_resolution_warning(_p, Path(_r), task_id)
                if _sw:
                    stale_warnings.append(_sw)

            file_ops = _get_file_ops(task_id)

            if mode == "replace":
                if not path:
                    return tool_error("path required")
                if old_string is None or new_string is None:
                    return tool_error("old_string and new_string required")

                # operates on the exact file the tool layer resolved — the
                # shell's own cwd may differ (worktree-cwd bug), and a relative

                # being edited.
                _replace_target = _path_to_resolved.get(path) or path
                result = file_ops.patch_replace(_replace_target, old_string, new_string, replace_all)
            elif mode == "patch":
                if not patch:
                    return tool_error("patch content required")
                result = file_ops.patch_v4a(patch)
            else:
                return tool_error(f"Unknown mode: {mode}")

            result_dict = result.to_dict()
            # 软守卫 cross-profile 警告拼接到最前（让模型最先看见）。
            all_warnings = cross_profile_warnings + stale_warnings
            if all_warnings:
                result_dict["_warning"] = all_warnings[0] if len(all_warnings) == 1 else " | ".join(all_warnings)
            # Report the ABSOLUTE path(s) actually patched so a wrong-cwd
            # mismatch (e.g. a worktree session editing the main checkout) is
            # visible in the response instead of silently landing elsewhere.
            _resolved_modified = [_path_to_resolved.get(_p) or _p for _p in _paths_to_check]

            # consecutive edits by this task don't trigger false warnings.
            if not result_dict.get("error"):
                result_dict["files_modified"] = _resolved_modified
                if len(_resolved_modified) == 1:
                    result_dict["resolved_path"] = _resolved_modified[0]
                for _p in _paths_to_check:
                    _update_read_timestamp(_p, task_id)
                    _r = _path_to_resolved.get(_p)
                    if _r:
                        note_write(task_id, _r)
                # Successful patch: clear any prior consecutive-failure

                # the same path starts the escalation cycle fresh.
                _reset_patch_failures(
                    task_id,
                    [_r for _r in (_path_to_resolved.get(_p) for _p in _paths_to_check) if _r],
                )
        # Hint when old_string not found — saves iterations where the agent
        # retries with stale content instead of re-reading the file.
        # Suppressed when patch_replace already attached a rich "Did you mean?"
        # snippet (which is strictly more useful than the generic hint).
        if result_dict.get("error") and "Could not find" in str(result_dict["error"]):
            # Track per-file consecutive failures for replace mode.  The
            # ``path`` arg only exists for replace mode; for V4A patches
            # we'd need to walk the headers, but in practice V4A failures
            # are far rarer and the existing _hint covers them adequately.
            failure_count = 0
            if mode == "replace" and path:
                resolved = _path_to_resolved.get(path) or path
                failure_count = _record_patch_failure(task_id, resolved)

            if failure_count >= 3:
                # same path.  Most common cause is a stale view of the file —

                # content that has since changed.  Surface the failure count
                # so the model recognises it's in a loop and breaks out by
                # re-reading or falling back to write_file.
                result_dict["_hint"] = (
                    f"This is failure #{failure_count} patching {path!r}. "
                    "Stop retrying with variations of the same old_string. "
                    "Either: (1) re-read the file fresh to verify current "
                    "content, (2) use a longer / more unique old_string with "
                    "surrounding context lines, or (3) use write_file to "
                    "replace the entire file if the targeted region is hard "
                    "to anchor."
                )
            elif "Did you mean one of these sections?" not in str(result_dict["error"]):
                result_dict["_hint"] = (
                    "old_string not found. Use read_file to verify the current content, "
                    "or search_files to locate the text."
                )
        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))


def search_tool(
    pattern: str,
    target: str = "content",
    path: str = ".",
    file_glob: str | None = None,
    limit: int = 50,
    offset: int = 0,
    output_mode: str = "content",
    context: int = 0,
    task_id: str = "default",
    cancel_token: Any = None,
) -> str:
    """搜索内容或文件名。"""
    try:
        offset, limit = normalize_search_pagination(offset, limit)

        # Track searches to detect *consecutive* repeated search loops.

        # results without tripping the repeated-search guard.
        search_key = ("search", pattern, target, str(path), file_glob or "", limit, offset)
        with _read_tracker_lock:
            task_data = _read_tracker.setdefault(
                task_id,
                {
                    "last_key": None,
                    "consecutive": 0,
                    "read_history": set(),
                    "dedup": {},
                    "dedup_hits": {},
                    "read_timestamps": {},
                },
            )
            if task_data["last_key"] == search_key:
                task_data["consecutive"] += 1
            else:
                task_data["last_key"] = search_key
                task_data["consecutive"] = 1
            count = task_data["consecutive"]

        if count >= 4:
            return json.dumps(
                {
                    "error": (
                        f"BLOCKED: You have run this exact search {count} times in a row. "
                        "The results have NOT changed. You already have this information. "
                        "STOP re-searching and proceed with your task."
                    ),
                    "pattern": pattern,
                    "already_searched": count,
                },
                ensure_ascii=False,
            )

        file_ops = _get_file_ops(task_id)
        result = file_ops.search(
            pattern=pattern,
            path=path,
            target=target,
            file_glob=file_glob,
            limit=limit,
            offset=offset,
            output_mode=output_mode,
            context=context,
        )
        if hasattr(result, "matches"):
            for m in result.matches:
                if hasattr(m, "content") and m.content:
                    m.content = redact_sensitive_text(m.content)
        result_dict = result.to_dict()

        if count >= 3:
            result_dict["_warning"] = (
                f"You have run this exact search {count} times consecutively. "
                "The results have not changed. Use the information you already have."
            )

        if result_dict.get("truncated"):
            next_offset = offset + limit
            result_dict["hint"] = (
                f"Results truncated. Use offset={next_offset} to see more, "
                "or narrow with a more specific pattern or file_glob."
            )
        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))


# Schemas + Registry

# Lazy import — 首次使用时解析，避免与 terminal_tool 循环依赖。


LIST_DIRECTORY_SCHEMA = {
    "name": "list_directory",
    "description": "List the contents of a directory. Returns file and folder names, sizes, and modification times.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the directory to list (absolute, relative, or ~/path)",
            },
        },
        "required": ["path"],
    },
}

READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": (
        "Read a text file with line numbers and pagination. Use this instead of cat/head/tail "
        "in terminal. Output format: 'LINE_NUM|CONTENT'. Suggests similar filenames if not "
        "found. Use offset and limit for large files. Reads exceeding ~100K characters are "
        "rejected; use offset and limit to read specific sections of large files. "
        "NOTE: Cannot read images or binary files — use vision_analyze for images."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read (absolute, relative, or ~/path)",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed, default: 1)",
                "default": 1,
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum number of lines to read (default: {DEFAULT_READ_LIMIT}, max: {MAX_LINES})",
                "default": DEFAULT_READ_LIMIT,
                "maximum": MAX_LINES,
            },
        },
        "required": ["path"],
    },
}

WRITE_FILE_SCHEMA = {
    "name": "write_file",
    "description": (
        "Write content to a file, completely replacing existing content. Use this instead of "
        "echo/cat heredoc in terminal. Creates parent directories automatically. OVERWRITES "
        "the entire file — use 'patch' for targeted edits. Auto-runs syntax checks on "
        ".py/.json/.yaml/.toml and other linted languages; only NEW errors introduced by this "
        "write are surfaced (pre-existing errors are filtered out)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path to the file to write (will be created if it doesn't exist, overwritten if it does)"
                ),
            },
            "content": {"type": "string", "description": "Complete content to write to the file"},
            "cross_profile": {
                "type": "boolean",
                "description": (
                    "Opt out of the cross-profile soft guard. Defaults to false. Set true ONLY "
                    "after explicit user direction to edit another SpiritAgent profile's "
                    "skills/plugins/cron/memories — by default these writes are blocked with "
                    "a warning because they affect a different profile than the one this "
                    "session is running under."
                ),
                "default": False,
            },
        },
        "required": ["path", "content"],
    },
}

PATCH_SCHEMA = {
    "name": "patch",
    "description": (
        "Targeted find-and-replace edits in files. Use this instead of sed/awk in terminal. "
        "Uses fuzzy matching so minor whitespace/indentation differences won't break it. "
        "Returns a unified diff. Auto-runs syntax checks after editing.\n\n"
        "REPLACE MODE (mode='replace', default): find a unique string and replace it. "
        "REQUIRED PARAMETERS: mode, path, old_string, new_string.\n"
        "PATCH MODE (mode='patch'): apply V4A multi-file patches for bulk changes. "
        "REQUIRED PARAMETERS: mode, patch."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["replace", "patch"],
                "description": (
                    "Edit mode. 'replace' (default): requires path + old_string + new_string. "
                    "'patch': requires patch content only."
                ),
                "default": "replace",
            },
            "path": {"type": "string", "description": "REQUIRED when mode='replace'. File path to edit."},
            "old_string": {
                "type": "string",
                "description": (
                    "REQUIRED when mode='replace'. Exact text to find and replace. "
                    "Must be unique in the file unless replace_all=true. Include surrounding "
                    "context lines to ensure uniqueness."
                ),
            },
            "new_string": {
                "type": "string",
                "description": (
                    "REQUIRED when mode='replace'. Replacement text. Pass empty string '' to delete the matched text."
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences instead of requiring a unique match (default: false)",
                "default": False,
            },
            "patch": {
                "type": "string",
                "description": (
                    "REQUIRED when mode='patch'. V4A format patch content. Format:\n"
                    "*** Begin Patch\n*** Update File: path/to/file\n"
                    "@@ context hint @@\n"
                    " context line\n-removed line\n+added line\n"
                    "*** End Patch"
                ),
            },
            "cross_profile": {
                "type": "boolean",
                "description": (
                    "Opt out of the cross-profile soft guard. Defaults to false. Set true "
                    "ONLY after explicit user direction to edit another SpiritAgent profile's "
                    "skills/plugins/cron/memories."
                ),
                "default": False,
            },
        },
        "required": ["mode"],
    },
}

SEARCH_FILES_SCHEMA = {
    "name": "search_files",
    "description": (
        "Search file contents or find files by name. Use this instead of grep/rg/find/ls in "
        "terminal — faster than the shell equivalents.\n\n"
        "Content search (target='content'): Regex search inside files. Output modes: full "
        "matches with line numbers, file paths only, or match counts.\n\n"
        "File search (target='files'): Find files by glob pattern (e.g., '*.py', '*config*'). "
        "Also use this instead of ls — results sorted by modification time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern for content search, or glob pattern (e.g., '*.py') for file search",
            },
            "target": {
                "type": "string",
                "enum": ["content", "files"],
                "description": "'content' searches inside file contents, 'files' searches for files by name",
                "default": "content",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in (default: current working directory)",
                "default": ".",
            },
            "file_glob": {
                "type": "string",
                "description": "Filter files by pattern in grep mode (e.g., '*.py' to only search Python files)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 50)",
                "default": 50,
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N results for pagination (default: 0)",
                "default": 0,
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_only", "count"],
                "description": (
                    "Output format for grep mode: 'content' shows matching lines with line "
                    "numbers, 'files_only' lists file paths, 'count' shows match counts per file"
                ),
                "default": "content",
            },
            "context": {
                "type": "integer",
                "description": "Number of context lines before and after each match (grep mode only)",
                "default": 0,
            },
        },
        "required": ["pattern"],
    },
}


def _handle_list_directory(args: dict[str, Any], **kw: Any) -> str:
    return list_directory_tool(args.get("path", ""), kw.get("task_id", "default"), kw.get("cancel_token"))


def _handle_read_file(args: dict[str, Any], **kw: Any) -> str:
    return read_file_tool(
        args.get("path", ""),
        args.get("offset", 1),
        args.get("limit", 500),
        kw.get("task_id", "default"),
        kw.get("cancel_token"),
    )


def _handle_write_file(args: dict[str, Any], **kw: Any) -> str:
    if not isinstance(path := args.get("path"), str) or not path:
        return tool_error("write_file: missing 'path'.")
    if "content" not in args:
        return tool_error("write_file: missing 'content'.")
    if not isinstance(content := args["content"], str):
        return tool_error(f"write_file: 'content' must be string, got {type(content).__name__}.")
    return write_file_tool(
        path,
        content,
        kw.get("task_id", "default"),
        bool(args.get("cross_profile")),
        kw.get("cancel_token"),
    )


def _handle_patch(args: dict[str, Any], **kw: Any) -> str:
    return patch_tool(
        args.get("mode", "replace"),
        args.get("path"),
        args.get("old_string"),
        args.get("new_string"),
        args.get("replace_all", False),
        args.get("patch"),
        kw.get("task_id", "default"),
        bool(args.get("cross_profile")),
        kw.get("cancel_token"),
    )


def _handle_search_files(args: dict[str, Any], **kw: Any) -> str:
    target = {"grep": "content", "find": "files"}.get(args.get("target", "content"), args.get("target", "content"))
    return search_tool(
        args.get("pattern", ""),
        target,
        args.get("path", "."),
        args.get("file_glob"),
        args.get("limit", 50),
        args.get("offset", 0),
        args.get("output_mode", "content"),
        args.get("context", 0),
        kw.get("task_id", "default"),
        kw.get("cancel_token"),
    )


registry.register_tool("list_directory", schema=LIST_DIRECTORY_SCHEMA)(_handle_list_directory)
registry.register_tool("read_file", schema=READ_FILE_SCHEMA)(_handle_read_file)
registry.register_tool("write_file", schema=WRITE_FILE_SCHEMA)(_handle_write_file)
registry.register_tool("patch", schema=PATCH_SCHEMA)(_handle_patch)
registry.register_tool("search_files", schema=SEARCH_FILES_SCHEMA)(_handle_search_files)
