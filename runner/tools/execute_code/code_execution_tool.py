import base64
import contextlib
import functools
import json
import logging
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from envs import (
    active_environments,
    create_environment,
    creation_locks,
    creation_locks_lock,
    env_lock,
    get_env_config,
    last_activity,
    resolve_container_task_id,
    start_cleanup_thread,
    task_env_overrides,
)
from utils import (
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    cfg_get,
    clean_output,
    find_python,
    get_subprocess_home,
    is_env_passthrough,
    is_interrupted,
    load_config,
    terminate_tree,
)

from ..registry import registry, tool_error
from ..thread_context import propagate_context_to_thread

logger = logging.getLogger(__name__)

EXECUTION_MODES = ("project", "strict")
DEFAULT_EXECUTION_MODE = "project"

SANDBOX_ALLOWED_TOOLS = frozenset(["read_file", "write_file", "search_files", "patch", "terminal"])

DEFAULT_TIMEOUT = 300

DEFAULT_MAX_TOOL_CALLS = 50

MAX_STDOUT_BYTES = 50_000

MAX_STDERR_BYTES = 10_000

_SAFE_ENV_PREFIXES = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SHELL",
    "LOGNAME",
    "XDG_",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CONDA",
)

_SECRET_SUBSTRINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "PASSWD", "AUTH", "DSN", "WEBHOOK")

SPIRITAGENT_CHILD_ALLOWED = frozenset(
    {"SPIRITAGENT_HOME", "SPIRITAGENT_PROFILE", "SPIRITAGENT_CONFIG", "SPIRITAGENT_ENV"},
)

_WINDOWS_ESSENTIAL_ENV_VARS = frozenset(
    {
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "OS",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        "PUBLIC",
        "ALLUSERSPROFILE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "USERDOMAIN",
        "USERNAME",
        "HOMEDRIVE",
        "HOMEPATH",
        "COMPUTERNAME",
    },
)


def _scrub_child_env(
    source_env: dict[str, str],
    is_passthrough: Callable[[str], bool] | None = None,
    is_windows: bool | None = None,
) -> dict[str, str]:
    if is_passthrough is None:
        is_passthrough = is_env_passthrough
    if is_windows is None:
        is_windows = IS_WINDOWS
    scrubbed: dict[str, str] = {}
    _dropped_spiritagent = []
    for k, v in source_env.items():
        if is_passthrough(k):
            scrubbed[k] = v
            continue
        if any(s in k.upper() for s in _SECRET_SUBSTRINGS):
            continue
        if any(k.startswith(p) for p in _SAFE_ENV_PREFIXES):
            scrubbed[k] = v
            continue
        if k in SPIRITAGENT_CHILD_ALLOWED:
            scrubbed[k] = v
            continue
        if is_windows and k.upper() in _WINDOWS_ESSENTIAL_ENV_VARS:
            scrubbed[k] = v
            continue
        if k.startswith("SPIRITAGENT_"):
            _dropped_spiritagent.append(k)
    if _dropped_spiritagent:
        logger.debug(
            "execute_code: dropped %d non-allowlisted SPIRITAGENT_* var(s) from the "
            "sandbox child env (%s). This is intentional hardening (#27303); if "
            "a sandbox script legitimately needs one, declare it via "
            "env_passthrough in the skill/config so it passes by explicit opt-in.",
            len(_dropped_spiritagent),
            ", ".join(sorted(_dropped_spiritagent)),
        )
    return scrubbed


_TOOL_STUBS = {
    "read_file": (
        "read_file",
        "path: str, offset: int = 1, limit: int = 500",
        '"""Read a file (1-indexed lines). Returns dict with "content" and "total_lines"."""',
        '{"path": path, "offset": offset, "limit": limit}',
    ),
    "write_file": (
        "write_file",
        "path: str, content: str, cross_profile: bool = False",
        (
            '"""Write content to a file (always overwrites). Returns dict with status. '
            'cross_profile=True opts out of the cross-SpiritAgent-profile soft guard."""'
        ),
        '{"path": path, "content": content, "cross_profile": cross_profile}',
    ),
    "search_files": (
        "search_files",
        (
            'pattern: str, target: str = "content", path: str = ".", file_glob: str = None, '
            'limit: int = 50, offset: int = 0, output_mode: str = "content", context: int = 0'
        ),
        (
            '"""Search file contents (target="content") or find files by name '
            '(target=\'files\'). Returns dict with "matches"."""'
        ),
        (
            '{"pattern": pattern, "target": target, "path": path, "file_glob": file_glob, '
            '"limit": limit, "offset": offset, "output_mode": output_mode, "context": context}'
        ),
    ),
    "patch": (
        "patch",
        (
            "path: str = None, old_string: str = None, new_string: str = None, "
            'replace_all: bool = False, mode: str = "replace", patch: str = None, '
            "cross_profile: bool = False"
        ),
        (
            '"""Targeted find-and-replace (mode="replace") or V4A multi-file patches '
            '(mode="patch"). Returns dict with status. cross_profile=True opts out of the '
            'cross-SpiritAgent-profile soft guard."""'
        ),
        (
            '{"path": path, "old_string": old_string, "new_string": new_string, '
            '"replace_all": replace_all, "mode": mode, "patch": patch, '
            '"cross_profile": cross_profile}'
        ),
    ),
    "terminal": (
        "terminal",
        "command: str, timeout: int = None, workdir: str = None",
        '"""Run a shell command (foreground only). Returns dict with "output" and "exit_code"."""',
        '{"command": command, "timeout": timeout, "workdir": workdir}',
    ),
}


def generate_spiritagent_tools_module(transport: str = "uds") -> str:
    """为 sandbox 子进程生成 ``spiritagent_tools`` 桩模块(选 UDS / file 传输头)。"""
    tools_to_generate = sorted(SANDBOX_ALLOWED_TOOLS)
    stub_functions = []
    export_names = []
    for tool_name in tools_to_generate:
        if tool_name not in _TOOL_STUBS:
            continue
        func_name, sig, doc, args_expr = _TOOL_STUBS[tool_name]
        stub_functions.append(f"def {func_name}({sig}):\n    {doc}\n    return _call({func_name!r}, {args_expr})\n")
        export_names.append(func_name)
    header = _FILE_TRANSPORT_HEADER if transport == "file" else _UDS_TRANSPORT_HEADER
    return header + "\n".join(stub_functions)


_COMMON_HELPERS = '''\
import json
import os
import shlex
import threading
import time

# Auth token for the parent's RPC endpoint, injected via the sandbox env.
_RPC_TOKEN = os.environ.get("SPIRITAGENT_RPC_TOKEN", "")

# Convenience helpers (avoid common scripting pitfalls)

def json_parse(text: str):
    """Parse JSON tolerant of control characters (strict=False).
    Use this instead of json.loads() when parsing output from terminal()
    that may contain raw tabs/newlines in strings."""
    return json.loads(text, strict=False)

def shell_quote(s: str) -> str:
    """Shell-escape a string for safe interpolation into commands.
    Use this when inserting dynamic content into terminal() commands:
        terminal(f"echo {shell_quote(user_input)}")
    """
    return shlex.quote(s)

def retry(fn, max_attempts=3, delay=2):
    """Retry a function up to max_attempts times with exponential backoff.
    Use for transient failures (network errors, API rate limits):
        result = retry(lambda: terminal("gh issue list ..."))
    """
    last_err = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < max_attempts - 1:
                time.sleep(delay * (2 ** attempt))
    raise last_err

'''

_UDS_TRANSPORT_HEADER = (
    _COMMON_HELPERS
    + '''\

import socket

_sock = None
_call_lock = threading.Lock()

def _connect():
    """Connect to the parent's RPC server via the transport it picked.

    SPIRITAGENT_RPC_SOCKET can be either:
      - a filesystem path (POSIX Unix domain socket — the default on
        macOS)
      - a string of the form ``tcp://127.0.0.1:<port>`` (Windows, where
        AF_UNIX is unreliable — the parent falls back to loopback TCP)
    """
    global _sock
    if _sock is None:
        endpoint = os.environ["SPIRITAGENT_RPC_SOCKET"]
        if endpoint.startswith("tcp://"):
            # tcp://host:port  (host is always 127.0.0.1 in practice — we
            # only bind loopback server-side)
            _host_port = endpoint[len("tcp://"):]
            _host, _, _port = _host_port.rpartition(":")
            _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _sock.connect((_host or "127.0.0.1", int(_port)))
        else:
            _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _sock.connect(endpoint)
        _sock.settimeout(300)
    # First line on the wire authenticates this sandbox to the parent's RPC
    # listener; unauthenticated local connections are dropped server-side.
    _sock.sendall((json.dumps({"auth": _RPC_TOKEN}) + "\\n").encode())
    return _sock

def _call(tool_name, args):
    """Send a tool call to the parent process and return the parsed result."""
    request = json.dumps({"tool": tool_name, "args": args}) + "\\n"
    with _call_lock:
        conn = _connect()
        conn.sendall(request.encode())
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                raise RuntimeError("Agent process disconnected")
            buf += chunk
            if buf.endswith(b"\\n"):
                break
    raw = buf.decode().strip()
    result = json.loads(raw)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result
    return result

'''
)

_FILE_TRANSPORT_HEADER = (
    _COMMON_HELPERS
    + '''\

_seq = 0
_seq_lock = threading.Lock()
_RPC_DIR = os.environ["SPIRITAGENT_RPC_DIR"]

def _call(tool_name, args):
    """Send a tool call request via file-based RPC and wait for response."""
    global _seq
    with _seq_lock:
        _seq += 1
        seq = _seq
    seq_str = f"{seq:06d}"
    req_file = os.path.join(_RPC_DIR, f"req_{seq_str}")
    res_file = os.path.join(_RPC_DIR, f"res_{seq_str}")

    # encoding="utf-8" is critical: on Windows-hosted remote backends
    # (or any non-UTF-8 locale) the default open() mode would mangle
    # non-ASCII chars in tool args when encoding them as JSON.
    tmp = req_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"tool": tool_name, "args": args, "seq": seq, "token": _RPC_TOKEN}, f)
    os.rename(tmp, req_file)

    deadline = time.monotonic() + 300  # 5-minute timeout per tool call
    poll_interval = 0.05  # Start at 50ms
    while not os.path.exists(res_file):
        if time.monotonic() > deadline:
            raise RuntimeError(f"RPC timeout: no response for {tool_name} after 300s")
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.2, 0.25)  # Back off to 250ms

    with open(res_file, encoding="utf-8") as f:
        raw = f.read()

    try:
        os.unlink(res_file)
    except OSError:
        pass

    result = json.loads(raw)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result
    return result

'''
)

_TERMINAL_BLOCKED_PARAMS = {"background", "pty", "notify_on_complete", "watch_patterns"}


def _read_conn_line(conn: socket.socket, buf: bytes) -> tuple[bytes | None, bytes]:
    """读取一个以换行结尾的行; 超时或对端关闭时返回 ``(None, buf)``。"""
    while b"\n" not in buf:
        try:
            chunk = conn.recv(65536)
        except TimeoutError:
            return None, buf
        if not chunk:
            return None, buf
        buf += chunk
    line, rest = buf.split(b"\n", 1)
    return line, rest


def _rpc_server_loop(
    server_sock: socket.socket,
    task_id: str,
    tool_call_log: list[Any],
    tool_call_counter: list[int],
    max_tool_calls: int,
    allowed_tools: frozenset[str],
    expected_token: str,
) -> None:
    """本机沙箱的父进程侧 RPC 监听循环: accept 一条已认证连接 + 串行派发工具调用。"""
    conn = None
    try:
        server_sock.settimeout(5)
        # Windows 上端点是 loopback TCP, 没有文件系统权限, 因此首行必须做沙箱认证;
        # 拒绝的连接立即关闭、监听继续接收 — 否则任何本地进程都能抢这个独享 RPC 槽位。
        while True:
            try:
                conn, _ = server_sock.accept()
            except TimeoutError:
                return
            conn.settimeout(5)
            line, buf = _read_conn_line(conn, b"")
            try:
                authed = line is not None and json.loads(line.decode()).get("auth") == expected_token
            except (json.JSONDecodeError, UnicodeDecodeError):
                authed = False
            if authed:
                break
            logger.debug("execute_code RPC: rejected unauthenticated connection")
            conn.close()
            conn = None
        conn.settimeout(300)
        # 先把可能和 auth 行 pipeline 一同到达的请求行消费掉, 再开始等新数据。
        while True:
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                call_start = time.monotonic()
                try:
                    request = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    resp = tool_error(f"Invalid RPC request: {exc}")
                    conn.sendall((resp + "\n").encode())
                    continue
                tool_name = request.get("tool", "")
                tool_args = request.get("args", {})
                if tool_name not in allowed_tools:
                    available = ", ".join(sorted(allowed_tools))
                    resp = json.dumps(
                        {"error": (f"Tool '{tool_name}' is not available in execute_code. Available: {available}")},
                    )
                    conn.sendall((resp + "\n").encode())
                    continue
                if tool_call_counter[0] >= max_tool_calls:
                    resp = json.dumps(
                        {
                            "error": (
                                f"Tool call limit reached ({max_tool_calls}). "
                                "No more tool calls allowed in this execution."
                            ),
                        },
                    )
                    conn.sendall((resp + "\n").encode())
                    continue
                if tool_name == "terminal" and isinstance(tool_args, dict):
                    for param in _TERMINAL_BLOCKED_PARAMS:
                        tool_args.pop(param, None)
                try:
                    _real_stdout, _real_stderr = sys.stdout, sys.stderr
                    with open(os.devnull, "w", encoding="utf-8") as devnull:
                        try:
                            sys.stdout = devnull
                            sys.stderr = devnull
                            result = registry.dispatch(tool_name, tool_args, task_id=task_id)
                        finally:
                            sys.stdout, sys.stderr = _real_stdout, _real_stderr
                except Exception as exc:
                    logger.error("Tool call failed in sandbox: %s", exc, exc_info=True)
                    result = tool_error(str(exc))
                tool_call_counter[0] += 1
                call_duration = time.monotonic() - call_start
                args_preview = str(tool_args)[:80]
                tool_call_log.append(
                    {"tool": tool_name, "args_preview": args_preview, "duration": round(call_duration, 2)},
                )
                conn.sendall((result + "\n").encode())
            try:
                chunk = conn.recv(65536)
            except TimeoutError:
                break
            if not chunk:
                break
            buf += chunk
    except TimeoutError:
        logger.debug("RPC listener socket timeout")
    except OSError as e:
        logger.debug("RPC listener socket error: %s", e, exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except OSError as e:
                logger.debug("RPC conn close error: %s", e)


def _get_or_create_env(task_id: str) -> tuple[Any, str]:
    """获取或按需创建 task_id 对应的沙箱 Environment(双检锁, 避免重复创建)。"""
    effective_task_id = resolve_container_task_id(task_id)
    with env_lock:
        if effective_task_id in active_environments:
            last_activity[effective_task_id] = time.time()
            return active_environments[effective_task_id], get_env_config()["env_type"]
    with creation_locks_lock:
        if effective_task_id not in creation_locks:
            creation_locks[effective_task_id] = threading.Lock()
        task_lock = creation_locks[effective_task_id]
    with task_lock:
        with env_lock:
            if effective_task_id in active_environments:
                last_activity[effective_task_id] = time.time()
                return active_environments[effective_task_id], get_env_config()["env_type"]
        config = get_env_config()
        env_type = config["env_type"]
        overrides = task_env_overrides.get(effective_task_id, {})
        cwd = overrides.get("cwd") or config["cwd"]
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
        logger.info("Creating new %s environment for execute_code task %s...", env_type, effective_task_id[:8])
        env = create_environment(
            env_type=env_type,
            cwd=cwd,
            timeout=config["timeout"],
            ssh_config=ssh_config,
            local_config=local_config,
            task_id=effective_task_id,
        )
        with env_lock:
            active_environments[effective_task_id] = env
            last_activity[effective_task_id] = time.time()
        start_cleanup_thread()
        logger.info("%s environment ready for execute_code task %s", env_type, effective_task_id[:8])
        return env, env_type


def _ship_file_to_remote(env: Any, remote_path: str, content: str) -> None:
    """通过 base64 把一段文本内容投递到沙箱里的 remote_path, 避开 shell 转义的所有坑。"""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    quoted_remote_path = shlex.quote(remote_path)
    env.execute(f"echo '{encoded}' | base64 -d > {quoted_remote_path}", cwd="/", timeout=30)


def _env_temp_dir(env: Any) -> str:
    """返回 env 后端可写的临时目录(优先 ``env.get_temp_dir()``, 回落到 ``tempfile.gettempdir()`` / ``/tmp``)。"""
    get_temp_dir = getattr(env, "get_temp_dir", None)
    if callable(get_temp_dir):
        try:
            temp_dir = get_temp_dir()
            if isinstance(temp_dir, str) and temp_dir.startswith("/"):
                return temp_dir.rstrip("/") or "/"
        except Exception as exc:
            logger.debug("Could not resolve execute_code env temp dir: %s", exc)
    candidate = tempfile.gettempdir()
    if isinstance(candidate, str) and candidate.startswith("/"):
        return candidate.rstrip("/") or "/"
    return "/tmp"


def _rpc_poll_loop(
    env: Any,
    rpc_dir: str,
    task_id: str,
    tool_call_log: list[Any],
    tool_call_counter: list[int],
    max_tool_calls: int,
    allowed_tools: frozenset[str],
    stop_event: threading.Event,
    expected_token: str,
) -> None:
    """远程沙箱的 RPC 轮询循环(基于文件 req/res), 触发父进程派发工具调用并写回响应文件。"""
    poll_interval = 0.1
    quoted_rpc_dir = shlex.quote(rpc_dir)
    while not stop_event.is_set():
        try:
            ls_result = env.execute(f"ls -1 {quoted_rpc_dir}/req_* 2>/dev/null || true", cwd="/", timeout=10)
            output = ls_result.get("output", "").strip()
            if not output:
                stop_event.wait(poll_interval)
                continue
            req_files = sorted(
                [
                    f.strip()
                    for f in output.split("\n")
                    if f.strip() and not f.strip().endswith(".tmp") and "/req_" in f.strip()
                ],
            )
            for req_file in req_files:
                if stop_event.is_set():
                    break
                call_start = time.monotonic()
                quoted_req_file = shlex.quote(req_file)
                read_result = env.execute(f"cat {quoted_req_file}", cwd="/", timeout=10)
                try:
                    request = json.loads(read_result.get("output", ""))
                except (json.JSONDecodeError, ValueError):
                    logger.debug("Malformed RPC request in %s", req_file)
                    env.execute(f"rm -f {quoted_req_file}", cwd="/", timeout=5)
                    continue
                tool_name = request.get("tool", "")
                tool_args = request.get("args", {})
                seq = request.get("seq", 0)
                seq_str = f"{seq:06d}"
                res_file = f"{rpc_dir}/res_{seq_str}"
                quoted_res_file = shlex.quote(res_file)
                if request.get("token") != expected_token:
                    # 沙箱侧认证: 与 TCP/UDS 路径同理, 不认证就拒, 防止沙箱内的恶意脚本冒充其他会话触发工具调用。
                    logger.debug("execute_code RPC: dropped unauthenticated request %s", req_file)
                    env.execute(f"rm -f {quoted_req_file}", cwd="/", timeout=5)
                    continue
                if tool_name not in allowed_tools:
                    available = ", ".join(sorted(allowed_tools))
                    tool_result = json.dumps(
                        {"error": (f"Tool '{tool_name}' is not available in execute_code. Available: {available}")},
                    )
                elif tool_call_counter[0] >= max_tool_calls:
                    tool_result = json.dumps(
                        {
                            "error": (
                                f"Tool call limit reached ({max_tool_calls}). "
                                "No more tool calls allowed in this execution."
                            ),
                        },
                    )
                else:
                    if tool_name == "terminal" and isinstance(tool_args, dict):
                        for param in _TERMINAL_BLOCKED_PARAMS:
                            tool_args.pop(param, None)
                    try:
                        _real_stdout, _real_stderr = sys.stdout, sys.stderr
                        with open(os.devnull, "w", encoding="utf-8") as devnull:
                            try:
                                sys.stdout = devnull
                                sys.stderr = devnull
                                tool_result = registry.dispatch(tool_name, tool_args, task_id=task_id)
                            finally:
                                sys.stdout, sys.stderr = _real_stdout, _real_stderr
                    except Exception as exc:
                        logger.error("Tool call failed in remote sandbox: %s", exc, exc_info=True)
                        tool_result = tool_error(str(exc))
                    tool_call_counter[0] += 1
                    call_duration = time.monotonic() - call_start
                    tool_call_log.append(
                        {"tool": tool_name, "args_preview": str(tool_args)[:80], "duration": round(call_duration, 2)},
                    )
                encoded_result = base64.b64encode(tool_result.encode("utf-8")).decode("ascii")
                env.execute(
                    (
                        f"echo '{encoded_result}' | base64 -d > {quoted_res_file}.tmp && "
                        f"mv {quoted_res_file}.tmp {quoted_res_file}"
                    ),
                    cwd="/",
                    timeout=60,
                )
                env.execute(f"rm -f {quoted_req_file}", cwd="/", timeout=5)
        except Exception as e:
            if not stop_event.is_set():
                logger.debug("RPC poll error: %s", e, exc_info=True)
        if not stop_event.is_set():
            stop_event.wait(poll_interval)


def _execute_remote(code: str) -> str:
    """在 SSH 沙箱后端里执行 ``code``; 通过文件 RPC 转发工具调用。"""
    _cfg = _load_config()
    timeout = _cfg.get("timeout", DEFAULT_TIMEOUT)
    max_tool_calls = _cfg.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    effective_task_id = "default"
    env, env_type = _get_or_create_env(effective_task_id)
    rpc_token = secrets.token_hex(16)
    sandbox_id = uuid.uuid4().hex[:12]
    temp_dir = _env_temp_dir(env)
    sandbox_dir = f"{temp_dir}/spiritagent_exec_{sandbox_id}"
    quoted_sandbox_dir = shlex.quote(sandbox_dir)
    quoted_rpc_dir = shlex.quote(f"{sandbox_dir}/rpc")
    tool_call_log: list = []
    tool_call_counter = [0]
    exec_start = time.monotonic()
    stop_event = threading.Event()
    rpc_thread = None
    try:
        py_check = env.execute("command -v python3 >/dev/null 2>&1 && echo OK", cwd="/", timeout=15)
        if "OK" not in py_check.get("output", ""):
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        f"Python 3 is not available in the {env_type} terminal environment. "
                        "Install Python to use execute_code with remote backends."
                    ),
                    "tool_calls_made": 0,
                    "duration_seconds": 0,
                },
            )
        env.execute(f"mkdir -p {quoted_rpc_dir}", cwd="/", timeout=10)
        tools_src = generate_spiritagent_tools_module(transport="file")
        _ship_file_to_remote(env, f"{sandbox_dir}/spiritagent_tools.py", tools_src)
        _ship_file_to_remote(env, f"{sandbox_dir}/script.py", code)
        rpc_thread = threading.Thread(
            target=propagate_context_to_thread(_rpc_poll_loop),
            args=(
                env,
                f"{sandbox_dir}/rpc",
                effective_task_id,
                tool_call_log,
                tool_call_counter,
                max_tool_calls,
                SANDBOX_ALLOWED_TOOLS,
                stop_event,
                rpc_token,
            ),
            daemon=True,
        )
        rpc_thread.start()
        env_prefix = (
            f"SPIRITAGENT_RPC_DIR={shlex.quote(f'{sandbox_dir}/rpc')} "
            f"SPIRITAGENT_RPC_TOKEN={rpc_token} PYTHONDONTWRITEBYTECODE=1"
        )
        tz = str(cfg_get(load_config(), "terminal", "timezone", default="")).strip()
        if tz:
            env_prefix += f" TZ={tz}"
        logger.info("Executing code on %s backend (task %s)...", env_type, effective_task_id[:8])
        script_result = env.execute(f"cd {quoted_sandbox_dir} && {env_prefix} python3 script.py", timeout=timeout)
        stdout_text = script_result.get("output", "")
        exit_code = script_result.get("returncode", -1)
        status = "success"
        if exit_code == 124:
            status = "timeout"
        elif exit_code == 130:
            status = "interrupted"
    except Exception as exc:
        duration = round(time.monotonic() - exec_start, 2)
        logger.error(
            "execute_code remote failed after %ss with %d tool calls: %s: %s",
            duration,
            tool_call_counter[0],
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return json.dumps(
            {
                "status": "error",
                "error": str(exc),
                "tool_calls_made": tool_call_counter[0],
                "duration_seconds": duration,
            },
            ensure_ascii=False,
        )
    finally:
        stop_event.set()
        if rpc_thread is not None:
            rpc_thread.join(timeout=5)
        try:
            env.execute(f"rm -rf {quoted_sandbox_dir}", cwd="/", timeout=15)
        except Exception:
            logger.debug("Failed to clean up remote sandbox %s", sandbox_dir)
    duration = round(time.monotonic() - exec_start, 2)
    if len(stdout_text) > MAX_STDOUT_BYTES:
        head_bytes = int(MAX_STDOUT_BYTES * 0.4)
        tail_bytes = MAX_STDOUT_BYTES - head_bytes
        head = stdout_text[:head_bytes]
        tail = stdout_text[-tail_bytes:]
        omitted = len(stdout_text) - len(head) - len(tail)
        stdout_text = (
            head
            + f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted out of {len(stdout_text):,} total] ...\n\n"
            + tail
        )
    stdout_text = clean_output(stdout_text)
    result: dict[str, Any] = {
        "status": status,
        "output": stdout_text,
        "tool_calls_made": tool_call_counter[0],
        "duration_seconds": duration,
    }
    if status == "timeout":
        timeout_msg = f"Script timed out after {timeout}s and was killed."
        result["error"] = timeout_msg
        if stdout_text:
            result["output"] = stdout_text + f"\n\n⏰ {timeout_msg}"
        else:
            result["output"] = f"⏰ {timeout_msg}"
        logger.warning(
            "execute_code (remote) timed out after %ss (limit %ss) with %d tool calls",
            duration,
            timeout,
            tool_call_counter[0],
        )
    elif status == "interrupted":
        result["output"] = stdout_text + "\n[execution interrupted — user sent a new message]"
    elif exit_code != 0:
        result["status"] = "error"
        result["error"] = f"Script exited with code {exit_code}"
    return json.dumps(result, ensure_ascii=False)


def execute_code(code: str) -> str:
    """执行 sandbox 子进程: 本机用 UDS/TCP RPC, SSH 后端委派 ``_execute_remote``。"""
    if not code or not code.strip():
        return tool_error("No code provided.")
    env_type = get_env_config()["env_type"]
    if env_type != "local":
        return _execute_remote(code)
    _cfg = _load_config()
    timeout = _cfg.get("timeout", DEFAULT_TIMEOUT)
    max_tool_calls = _cfg.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    tmpdir = tempfile.mkdtemp(prefix="spiritagent_sandbox_")
    _sock_tmpdir = "/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    _use_tcp_rpc = IS_WINDOWS
    if _use_tcp_rpc:
        sock_path = None
        rpc_endpoint = None
    else:
        sock_path = os.path.join(_sock_tmpdir, f"spiritagent_rpc_{uuid.uuid4().hex}.sock")
        rpc_endpoint = sock_path
    tool_call_log: list = []
    tool_call_counter = [0]
    exec_start = time.monotonic()
    server_sock = None
    rpc_token = secrets.token_hex(16)
    try:
        tools_src = generate_spiritagent_tools_module()
        with open(os.path.join(tmpdir, "spiritagent_tools.py"), "w", encoding="utf-8") as f:
            f.write(tools_src)
        with open(os.path.join(tmpdir, "script.py"), "w", encoding="utf-8") as f:
            f.write(code)
        if _use_tcp_rpc:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.bind(("127.0.0.1", 0))
            _host, _port = server_sock.getsockname()[:2]
            rpc_endpoint = f"tcp://{_host}:{_port}"
        else:
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(sock_path)
            os.chmod(sock_path, 0o600)
        server_sock.listen(1)
        rpc_thread = threading.Thread(
            target=propagate_context_to_thread(_rpc_server_loop),
            args=(
                server_sock,
                "default",
                tool_call_log,
                tool_call_counter,
                max_tool_calls,
                SANDBOX_ALLOWED_TOOLS,
                rpc_token,
            ),
            daemon=True,
        )
        rpc_thread.start()
        child_env = _scrub_child_env(os.environ)
        child_env["SPIRITAGENT_RPC_SOCKET"] = rpc_endpoint
        child_env["SPIRITAGENT_RPC_TOKEN"] = rpc_token
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        _spiritagent_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _existing_pp = child_env.get("PYTHONPATH", "")
        _pp_parts = [tmpdir, _spiritagent_root]
        if _existing_pp:
            _pp_parts.append(_existing_pp)
        child_env["PYTHONPATH"] = os.pathsep.join(_pp_parts)
        _tz_name = str(cfg_get(load_config(), "terminal", "timezone", default="")).strip()
        if _tz_name:
            child_env["TZ"] = _tz_name
        child_env.pop("SPIRITAGENT_TIMEZONE", None)
        _profile_home = get_subprocess_home()
        if _profile_home:
            child_env["HOME"] = str(_profile_home)
        _mode = _get_execution_mode()
        _child_python = _resolve_child_python(_mode)
        _child_cwd = _resolve_child_cwd(_mode, tmpdir)
        _script_path = os.path.join(tmpdir, "script.py")
        proc = subprocess.Popen(
            [_child_python, _script_path],
            cwd=_child_cwd,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            preexec_fn=None if IS_WINDOWS else os.setsid,
            creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0,
        )
        deadline = time.monotonic() + timeout
        stderr_chunks: list = []
        _STDOUT_HEAD_BYTES = int(MAX_STDOUT_BYTES * 0.4)
        _STDOUT_TAIL_BYTES = MAX_STDOUT_BYTES - _STDOUT_HEAD_BYTES

        def _drain(pipe, chunks, max_bytes) -> None:
            total = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    if total < max_bytes:
                        keep = max_bytes - total
                        chunks.append(data[:keep])
                    total += len(data)
            except (ValueError, OSError) as e:
                logger.debug("Error reading process output: %s", e, exc_info=True)

        stdout_total_bytes = [0]

        def _drain_head_tail(pipe, head_chunks, tail_chunks, head_bytes, tail_bytes, total_ref) -> None:
            head_collected = 0
            tail_buf = deque()
            tail_collected = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    total_ref[0] += len(data)
                    if head_collected < head_bytes:
                        keep = min(len(data), head_bytes - head_collected)
                        head_chunks.append(data[:keep])
                        head_collected += keep
                        data = data[keep:]
                        if not data:
                            continue
                    tail_buf.append(data)
                    tail_collected += len(data)
                    while tail_collected > tail_bytes and tail_buf:
                        oldest = tail_buf.popleft()
                        tail_collected -= len(oldest)
            except (ValueError, OSError):
                pass
            tail_chunks.extend(tail_buf)

        stdout_head_chunks: list = []
        stdout_tail_chunks: list = []
        stdout_reader = threading.Thread(
            target=_drain_head_tail,
            args=(
                proc.stdout,
                stdout_head_chunks,
                stdout_tail_chunks,
                _STDOUT_HEAD_BYTES,
                _STDOUT_TAIL_BYTES,
                stdout_total_bytes,
            ),
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=_drain,
            args=(proc.stderr, stderr_chunks, MAX_STDERR_BYTES),
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader.start()
        status = "success"
        while proc.poll() is None:
            if is_interrupted():
                _kill_process_group(proc)
                status = "interrupted"
                break
            if time.monotonic() > deadline:
                _kill_process_group(proc, escalate=True)
                status = "timeout"
                break
            time.sleep(0.2)
        stdout_reader.join(timeout=3)
        stderr_reader.join(timeout=3)
        stdout_head = b"".join(stdout_head_chunks).decode("utf-8", errors="replace")
        stdout_tail = b"".join(stdout_tail_chunks).decode("utf-8", errors="replace")
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        total_stdout = stdout_total_bytes[0]
        if total_stdout > MAX_STDOUT_BYTES and stdout_tail:
            omitted = total_stdout - len(stdout_head) - len(stdout_tail)
            truncated_notice = (
                f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted out of {total_stdout:,} total] ...\n\n"
            )
            stdout_text = stdout_head + truncated_notice + stdout_tail
        else:
            stdout_text = stdout_head + stdout_tail
        exit_code = proc.returncode if proc.returncode is not None else -1
        duration = round(time.monotonic() - exec_start, 2)
        server_sock.close()
        server_sock = None
        rpc_thread.join(timeout=3)
        stdout_text = clean_output(stdout_text)
        stderr_text = clean_output(stderr_text)
        result: dict[str, Any] = {
            "status": status,
            "output": stdout_text,
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }
        if status == "timeout":
            timeout_msg = f"Script timed out after {timeout}s and was killed."
            result["error"] = timeout_msg
            if stdout_text:
                result["output"] = stdout_text + f"\n\n⏰ {timeout_msg}"
            else:
                result["output"] = f"⏰ {timeout_msg}"
            logger.warning(
                "execute_code timed out after %ss (limit %ss) with %d tool calls",
                duration,
                timeout,
                tool_call_counter[0],
            )
        elif status == "interrupted":
            result["output"] = stdout_text + "\n[execution interrupted — user sent a new message]"
        elif exit_code != 0:
            result["status"] = "error"
            result["error"] = stderr_text or f"Script exited with code {exit_code}"
            if stderr_text:
                result["output"] = stdout_text + "\n--- stderr ---\n" + stderr_text
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        duration = round(time.monotonic() - exec_start, 2)
        logger.error(
            "execute_code failed after %ss with %d tool calls: %s: %s",
            duration,
            tool_call_counter[0],
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return json.dumps(
            {
                "status": "error",
                "error": str(exc),
                "tool_calls_made": tool_call_counter[0],
                "duration_seconds": duration,
            },
            ensure_ascii=False,
        )
    finally:
        if server_sock is not None:
            try:
                server_sock.close()
            except OSError as e:
                logger.debug("Server socket close error: %s", e)
        shutil.rmtree(tmpdir, ignore_errors=True)
        try:
            if sock_path:
                os.unlink(sock_path)
        except OSError:
            pass


def _kill_process_group(proc: subprocess.Popen, escalate: bool = False) -> None:
    """杀掉沙箱子进程及其整个进程组.

    委派 ``utils.process_tree.terminate_tree``: POSIX 走 killpg(SIGTERM) → wait →
    (escalate) killpg(SIGKILL) + psutil 兜底; Windows 走 taskkill /T → wait →
    (escalate) taskkill /T /F。``escalate=False`` 走 interrupt 路径(只软杀, 不升级);
    ``escalate=True`` 走 timeout 路径(TERM→KILL 升级)。
    """
    try:
        terminate_tree(
            proc,
            graceful_timeout=1.0,
            # force_timeout 与原 ``proc.wait(timeout=5)`` 的 5s 等待对齐。
            force_timeout=5.0,
            escalate=escalate,
        )
    except Exception as e:
        logger.debug("terminate_tree failed: %s", e, exc_info=True)
        with contextlib.suppress(Exception):
            proc.kill()


def _load_config() -> dict:
    """从内存 config 读 ``code_execution`` 段; 解析失败或缺失时返回空 dict。"""
    try:
        cfg = load_config().get("code_execution", {})
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _get_execution_mode() -> str:
    """读取合法的 ``code_execution.mode``, 非法值降级为默认。"""
    cfg_value = str(_load_config().get("mode", DEFAULT_EXECUTION_MODE)).strip().lower()
    if cfg_value in EXECUTION_MODES:
        return cfg_value
    logger.warning(
        "Ignoring code_execution.mode=%r (expected one of %s), falling back to %r",
        cfg_value,
        EXECUTION_MODES,
        DEFAULT_EXECUTION_MODE,
    )
    return DEFAULT_EXECUTION_MODE


@functools.lru_cache(maxsize=32)
def _is_usable_python(python_path: str, venv: str, conda: str) -> bool:
    """探测一个 Python 解释器是否 >= 3.8 可用; ``venv`` / ``conda`` 仅作为缓存键区分条目
    (进程内 venv 切换会自动失效缓存)。
    """
    # 把 venv/conda 放进 cache key, 让切换 VIRTUAL_ENV / CONDA_PREFIX 的 worktree swap 不需要手动 cache_clear()。
    del venv, conda
    try:
        result = subprocess.run(
            [python_path, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"],
            timeout=5,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


def _resolve_child_python(mode: str) -> str:
    """解析沙箱子进程用的 Python 解释器(优先 installer 装的 uv-managed venv, 再看 VIRTUAL_ENV/CONDA_PREFIX)。"""
    if mode != "project":
        return sys.executable
    # 用鉴别串作为 discriminator, 避免和 ``VIRTUAL_ENV``/``CONDA_PREFIX`` 探测条目撞 key(env 变量缺失时这两路用空串)。
    managed = find_python()
    if managed and _is_usable_python(managed, "managed-venv", ""):
        return managed
    if IS_WINDOWS:
        exe_names = ("python.exe", "python3.exe")
        subdirs = ("Scripts",)
    else:
        exe_names = ("python", "python3")
        subdirs = ("bin",)
    for var in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        root = os.environ.get(var, "").strip()
        if not root:
            continue
        for subdir in subdirs:
            for exe in exe_names:
                candidate = os.path.join(root, subdir, exe)
                if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
                    continue
                if _is_usable_python(candidate, os.environ.get("VIRTUAL_ENV", ""), os.environ.get("CONDA_PREFIX", "")):
                    return candidate
                logger.info(
                    "execute_code: skipping %s=%s (Python version < 3.8 or broken). Using sys.executable instead.",
                    var,
                    candidate,
                )
                return sys.executable
    return sys.executable


def _resolve_child_cwd(mode: str, staging_dir: str) -> str:
    """解析沙箱子进程的 cwd: project 模式按 ``terminal.cwd`` / 当前目录回退, 非 project 模式直接用 staging。"""
    if mode != "project":
        return staging_dir
    raw = str(cfg_get(load_config(), "terminal", "cwd", default="")).strip()
    if raw:
        expanded = os.path.expanduser(raw)
        if os.path.isdir(expanded):
            return expanded
    here = os.getcwd()
    if os.path.isdir(here):
        return here
    return staging_dir


EXECUTE_CODE_SCHEMA = {
    "name": "execute_code",
    "description": (
        "Run a Python script that can call SpiritAgent tools programmatically. Use this when "
        "you need 3+ tool calls with processing logic between them, need to filter/reduce "
        "large tool outputs before they enter your context, need conditional branching "
        "(if X then Y else Z), or need to loop (fetch N pages, process N files, retry on "
        "failure).\n\n"
        "Use normal tool calls instead when: single tool call with no processing, you need "
        "to see the full result and apply complex reasoning, or the task requires interactive "
        "user input.\n\n"
        "Available via `from spiritagent_tools import ...`:\n\n"
        "  read_file(path: str, offset: int = 1, limit: int = 500) -> dict\n"
        '    Lines are 1-indexed. Returns {"content": "...", "total_lines": N}\n'
        "  write_file(path: str, content: str) -> dict\n"
        "    Always overwrites the entire file.\n"
        '  search_files(pattern: str, target="content", path=".", file_glob=None, '
        "limit=50) -> dict\n"
        '    target: "content" (search inside files) or "files" (find files by name). '
        'Returns {"matches": [...]}\n'
        "  patch(path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict\n"
        "    Replaces old_string with new_string in the file.\n"
        "  terminal(command: str, timeout=None, workdir=None) -> dict\n"
        '    Foreground only (no background/pty). Returns {"output": "...", "exit_code": N}\n\n'
        "Limits: 5-minute timeout, 50KB stdout cap, max 50 tool calls per script. terminal() "
        "is foreground-only (no background or pty).\n\n"
        "Scripts run in the session's working directory with the active venv's python, so "
        "project deps (pandas, etc.) and relative paths work like in terminal().\n\n"
        "Print your final result to stdout. Use Python stdlib (json, re, math, csv, "
        "datetime, collections, etc.) for processing between tool calls.\n\n"
        "Also available (no import needed — built into spiritagent_tools):\n"
        "  json_parse(text: str) — json.loads with strict=False; use for terminal() output "
        "with control chars\n"
        "  shell_quote(s: str) — shlex.quote(); use when interpolating dynamic strings into "
        "shell commands\n"
        "  retry(fn, max_attempts=3, delay=2) — retry with exponential backoff for "
        "transient failures"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python code to execute. "
                    "Import tools with `from spiritagent_tools import read_file, terminal, ...` "
                    "and print your final result to stdout."
                ),
            },
        },
        "required": ["code"],
    },
}


registry.register_tool("execute_code", schema=EXECUTE_CODE_SCHEMA)(
    lambda args, **_kw: execute_code(code=args.get("code", "")),
)
