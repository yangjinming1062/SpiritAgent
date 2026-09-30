import base64
import contextlib
import functools
import json
import logging
import os
import secrets
import select
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
from collections.abc import Mapping
from typing import IO, Any, Literal

from envs import get_env_config, use_environment
from utils import (
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    cfg_get,
    cfg_int,
    cfg_str,
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

DEFAULT_TIMEOUT = 300

DEFAULT_MAX_TOOL_CALLS = 50

MAX_STDOUT_BYTES = 50_000
_STDOUT_HEAD_BYTES = int(MAX_STDOUT_BYTES * 0.4)
_STDOUT_TAIL_BYTES = MAX_STDOUT_BYTES - _STDOUT_HEAD_BYTES

MAX_STDERR_BYTES = 10_000

# 本机 RPC 线程的轮询粒度：关闭监听 socket 不能可靠唤醒阻塞中的 accept，靠短超时检查停止信号。
_RPC_POLL_SECONDS = 0.2

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

SPIRITAGENT_CHILD_ALLOWED = frozenset({"SPIRITAGENT_HOME"})

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


def _scrub_child_env(source_env: Mapping[str, str]) -> dict[str, str]:
    scrubbed: dict[str, str] = {}
    _dropped_spiritagent = []
    for k, v in source_env.items():
        if is_env_passthrough(k):
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
        if IS_WINDOWS and k.upper() in _WINDOWS_ESSENTIAL_ENV_VARS:
            scrubbed[k] = v
            continue
        if k.startswith("SPIRITAGENT_"):
            _dropped_spiritagent.append(k)
    if _dropped_spiritagent:
        logger.debug(
            "execute_code: dropped %d non-allowlisted SPIRITAGENT_* var(s) from the sandbox child env (%s); "
            "declare them in env_passthrough to pass them through.",
            len(_dropped_spiritagent),
            ", ".join(sorted(_dropped_spiritagent)),
        )
    return scrubbed


# 工具名 → (桩函数签名, docstring, 转发参数表达式)；桩函数名与工具名相同。
_TOOL_STUBS: dict[str, tuple[str, str, str]] = {
    "read_file": (
        "path: str, offset: int = 1, limit: int = 500",
        '"""Read a text file (1-indexed lines). Returns dict with "content" (lines prefixed "N|") and "total_lines"."""',
        '{"path": path, "offset": offset, "limit": limit}',
    ),
    "write_file": (
        "path: str, content: str",
        '"""Write content to a file (always overwrites)."""',
        '{"path": path, "content": content}',
    ),
    "search_files": (
        (
            'pattern: str, target: str = "content", path: str = ".", file_glob: str = None, '
            'limit: int = 50, offset: int = 0, output_mode: str = "content", context: int = 0'
        ),
        (
            '"""Search file contents (target="content") or find files by name (target="files"). '
            'Returns dict with "total_count" plus "matches" or "files" (omitted when empty)."""'
        ),
        (
            '{"pattern": pattern, "target": target, "path": path, "file_glob": file_glob, '
            '"limit": limit, "offset": offset, "output_mode": output_mode, "context": context}'
        ),
    ),
    "patch": (
        (
            "path: str = None, old_string: str = None, new_string: str = None, "
            'replace_all: bool = False, mode: str = "replace", patch: str = None'
        ),
        '"""Targeted find-and-replace (mode="replace") or V4A multi-file patches (mode="patch")."""',
        (
            '{"path": path, "old_string": old_string, "new_string": new_string, '
            '"replace_all": replace_all, "mode": mode, "patch": patch}'
        ),
    ),
    "terminal": (
        "command: str, timeout: int = None, workdir: str = None",
        '"""Run a shell command (foreground only). Returns dict with "output" and "exit_code"."""',
        '{"command": command, "timeout": timeout, "workdir": workdir}',
    ),
}

SANDBOX_ALLOWED_TOOLS = frozenset(_TOOL_STUBS)


def generate_spiritagent_tools_module(transport: Literal["uds", "file"]) -> str:
    """为 sandbox 子进程生成 ``spiritagent_tools`` 桩模块(选 UDS / file 传输头)。"""
    stub_functions = [
        f"def {name}({sig}):\n    {doc}\n    return _call({name!r}, {args_expr})\n"
        for name, (sig, doc, args_expr) in sorted(_TOOL_STUBS.items())
    ]
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
    """Call fn, retrying with exponential backoff only when it raises.
    Tool helpers report failures as a dict with an "error" key instead of
    raising, so check their results yourself. retry does not judge whether
    repeating an operation is safe.
    """
    for attempt in range(max_attempts - 1):
        try:
            return fn()
        except Exception:
            time.sleep(delay * (2 ** attempt))
    return fn()

'''

_UDS_TRANSPORT_HEADER = (
    _COMMON_HELPERS
    + '''\

import socket

_sock = None
_call_lock = threading.Lock()

def _connect():
    """Connect to the parent's RPC endpoint: a Unix socket path, or tcp://127.0.0.1:<port> on Windows."""
    global _sock
    if _sock is None:
        endpoint = os.environ["SPIRITAGENT_RPC_SOCKET"]
        if endpoint.startswith("tcp://"):
            _host, _, _port = endpoint[len("tcp://"):].rpartition(":")
            _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _sock.connect((_host or "127.0.0.1", int(_port)))
        else:
            _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _sock.connect(endpoint)
        _sock.settimeout(300)
        # The first line authenticates this sandbox; the parent drops connections without it.
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

    # encoding="utf-8": a non-UTF-8 locale would otherwise mangle non-ASCII tool args.
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

_TERMINAL_BLOCKED_PARAMS = {"background", "pty"}


def _token_matches(candidate: object, expected_token: str) -> bool:
    return isinstance(candidate, str) and secrets.compare_digest(candidate.encode(), expected_token.encode())


def _dispatch_sandbox_call(
    request: object,
    task_id: str,
    tool_call_counter: list[int],
    max_tool_calls: int,
) -> str:
    """执行沙箱发来的一次工具调用，返回 JSON 字符串；工具集禁用由 ``registry.dispatch`` 与直接调用同样执行。"""
    if not isinstance(request, dict):
        return tool_error("Invalid RPC request: expected a JSON object.")
    tool_name = request.get("tool")
    if not isinstance(tool_name, str) or tool_name not in SANDBOX_ALLOWED_TOOLS:
        available = ", ".join(sorted(SANDBOX_ALLOWED_TOOLS))
        return tool_error(f"Tool '{tool_name}' is not available in execute_code. Available: {available}")
    tool_args = request.get("args")
    if not isinstance(tool_args, dict):
        return tool_error("Tool 'args' must be an object.")
    if tool_call_counter[0] >= max_tool_calls:
        return tool_error(f"Tool call limit reached ({max_tool_calls}). No more tool calls allowed in this execution.")
    if tool_name == "terminal":
        for param in _TERMINAL_BLOCKED_PARAMS:
            tool_args.pop(param, None)
    tool_call_counter[0] += 1
    return registry.dispatch(tool_name, tool_args, task_id=task_id)


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


def _is_auth_frame(line: bytes, expected_token: str) -> bool:
    try:
        frame = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(frame, dict) and _token_matches(frame.get("auth"), expected_token)


def _rpc_server_loop(
    server_sock: socket.socket,
    task_id: str,
    tool_call_counter: list[int],
    max_tool_calls: int,
    expected_token: str,
    stop_event: threading.Event,
) -> None:
    """本机沙箱的父进程侧 RPC：只接受首帧携带本次 token 的一条连接，串行派发其工具调用，直到脚本结束。"""
    conn: socket.socket | None = None
    buf = b""
    try:
        server_sock.settimeout(_RPC_POLL_SECONDS)
        while conn is None:
            if stop_event.is_set():
                return
            try:
                candidate, _ = server_sock.accept()
            except TimeoutError:
                continue
            # Windows 端点是没有文件权限保护的 loopback TCP；未通过首帧鉴权的连接立即关闭，监听继续。
            candidate.settimeout(5)
            line, buf = _read_conn_line(candidate, b"")
            if line is not None and _is_auth_frame(line, expected_token):
                conn = candidate
            else:
                logger.debug("execute_code RPC: rejected unauthenticated connection")
                candidate.close()
        # 连接保持阻塞以便大结果完整发送；等待请求时用 select 轮询停止信号。脚本退出或被终止时连接随之关闭。
        conn.settimeout(None)
        while True:
            # 先消费可能与鉴权行一同到达的请求行，再等新数据。
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not (line := line.strip()):
                    continue
                try:
                    request = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    result = tool_error(f"Invalid RPC request: {exc}")
                else:
                    result = _dispatch_sandbox_call(request, task_id, tool_call_counter, max_tool_calls)
                conn.sendall((result + "\n").encode())
            if not select.select([conn], [], [], _RPC_POLL_SECONDS)[0]:
                if stop_event.is_set():
                    return
                continue
            if not (chunk := conn.recv(65536)):
                return
            buf += chunk
    except OSError as e:
        logger.debug("execute_code RPC listener stopped: %s", e, exc_info=True)
    finally:
        if conn is not None:
            with contextlib.suppress(OSError):
                conn.close()


# 远程沙箱的辅助命令都不传 cwd：env.execute 会把命令结束时的目录记为终端会话目录，
# 在会话目录里执行才不会把用户的终端目录改掉。内容经 stdin 传输，避开命令行长度上限。


def _ship_file_to_remote(env: Any, remote_path: str, content: str) -> None:
    """把文本内容写到远程沙箱的 remote_path；经 base64 + stdin 传输，避开转义与命令行长度限制。"""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    result = env.execute(f"base64 -d > {shlex.quote(remote_path)}", stdin_data=encoded, timeout=30)
    if result.get("returncode") != 0:
        raise RuntimeError(f"Failed to write {remote_path}: {result.get('output', '').strip()}")


def _serve_remote_request(
    env: Any,
    rpc_dir: str,
    req_file: str,
    task_id: str,
    tool_call_counter: list[int],
    max_tool_calls: int,
    expected_token: str,
) -> None:
    quoted_req_file = shlex.quote(req_file)
    raw = env.execute(f"cat {quoted_req_file}", timeout=10).get("output", "")
    # 读到即删：后续任一步失败都不会让同一请求在下一轮轮询被重复执行。
    env.execute(f"rm -f {quoted_req_file}", timeout=5)
    try:
        request = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("Malformed RPC request in %s", req_file)
        return
    if not isinstance(request, dict) or not _token_matches(request.get("token"), expected_token):
        logger.debug("execute_code RPC: dropped unauthenticated request %s", req_file)
        return
    if type(seq := request.get("seq")) is not int or seq < 0:
        logger.debug("execute_code RPC: dropped request %s without a valid seq", req_file)
        return
    tool_result = _dispatch_sandbox_call(request, task_id, tool_call_counter, max_tool_calls)
    quoted_res_file = shlex.quote(f"{rpc_dir}/res_{seq:06d}")
    env.execute(
        f"base64 -d > {quoted_res_file}.tmp && mv {quoted_res_file}.tmp {quoted_res_file}",
        stdin_data=base64.b64encode(tool_result.encode("utf-8")).decode("ascii"),
        timeout=60,
    )


def _rpc_poll_loop(
    env: Any,
    rpc_dir: str,
    task_id: str,
    tool_call_counter: list[int],
    max_tool_calls: int,
    stop_event: threading.Event,
    expected_token: str,
) -> None:
    """远程沙箱的 RPC 轮询循环(基于文件 req/res), 触发父进程派发工具调用并写回响应文件。"""
    poll_interval = 0.1
    quoted_rpc_dir = shlex.quote(rpc_dir)
    req_prefix = f"{rpc_dir}/req_"
    while not stop_event.is_set():
        try:
            listing = env.execute(f"ls -1 {quoted_rpc_dir}/req_* 2>/dev/null || true", timeout=10).get("output", "")
            req_files = sorted(
                f
                for raw in listing.splitlines()
                if (f := raw.strip()).startswith(req_prefix) and not f.endswith(".tmp")
            )
            for req_file in req_files:
                if stop_event.is_set():
                    break
                _serve_remote_request(
                    env,
                    rpc_dir,
                    req_file,
                    task_id,
                    tool_call_counter,
                    max_tool_calls,
                    expected_token,
                )
        except Exception as e:
            if not stop_event.is_set():
                logger.debug("RPC poll error: %s", e, exc_info=True)
        stop_event.wait(poll_interval)


def _truncation_notice(omitted: int, total: int) -> str:
    return f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted out of {total:,} total] ...\n\n"


def _error_result(error: str, tool_calls: int, duration: float) -> str:
    return json.dumps(
        {"status": "error", "error": error, "tool_calls_made": tool_calls, "duration_seconds": duration},
        ensure_ascii=False,
    )


def _build_result(
    *,
    status: str,
    output: str,
    stderr: str,
    exit_code: int,
    timeout: int,
    tool_calls: int,
    duration: float,
) -> str:
    output = clean_output(output)
    stderr = clean_output(stderr)
    result: dict[str, Any] = {
        "status": status,
        "output": output,
        "tool_calls_made": tool_calls,
        "duration_seconds": duration,
    }
    if status == "timeout":
        timeout_msg = f"Script timed out after {timeout}s and was killed."
        result["error"] = timeout_msg
        result["output"] = f"{output}\n\n⏰ {timeout_msg}" if output else f"⏰ {timeout_msg}"
        logger.warning(
            "execute_code timed out after %ss (limit %ss) with %d tool calls",
            duration,
            timeout,
            tool_calls,
        )
    elif status == "interrupted":
        result["output"] = output + "\n[execution interrupted]"
    elif exit_code != 0:
        result["status"] = "error"
        result["error"] = stderr or f"Script exited with code {exit_code}"
        if stderr:
            result["output"] = output + "\n--- stderr ---\n" + stderr
    return json.dumps(result, ensure_ascii=False)


def _execute_remote(code: str, timeout: int, max_tool_calls: int) -> str:
    """在 SSH 沙箱后端里执行 ``code``; 通过文件 RPC 转发工具调用。"""
    task_id = "default"
    tool_call_counter = [0]
    exec_start = time.monotonic()
    stop_event = threading.Event()
    rpc_thread: threading.Thread | None = None
    env: Any = None
    sandbox_dir: str | None = None
    # 持有环境直到远端沙箱清理完：脚本运行期间配置切换不会停止这个环境。
    held_env = contextlib.ExitStack()
    try:
        env = held_env.enter_context(use_environment(task_id))
        env_type = env.env_type
        py_check = env.execute("command -v python3 >/dev/null 2>&1 && echo OK", timeout=15)
        if "OK" not in py_check.get("output", ""):
            return _error_result(
                (
                    f"Python 3 is not available in the {env_type} terminal environment. "
                    "Install Python to use execute_code with remote backends."
                ),
                0,
                0,
            )
        rpc_token = secrets.token_hex(16)
        candidate_dir = f"{env.get_temp_dir().rstrip('/')}/spiritagent_exec_{uuid.uuid4().hex[:12]}"
        rpc_dir = f"{candidate_dir}/rpc"
        # 0700 且不带 -p：其他用户读不到脚本和 RPC 文件，也不能预先占用这个目录名。
        mkdir_result = env.execute(
            f"mkdir -m 700 {shlex.quote(candidate_dir)} && mkdir {shlex.quote(rpc_dir)}",
            timeout=10,
        )
        if mkdir_result.get("returncode") != 0:
            raise RuntimeError(f"Failed to create remote sandbox directory: {mkdir_result.get('output', '').strip()}")
        sandbox_dir = candidate_dir
        _ship_file_to_remote(env, f"{sandbox_dir}/spiritagent_tools.py", generate_spiritagent_tools_module("file"))
        _ship_file_to_remote(env, f"{sandbox_dir}/script.py", code)
        rpc_thread = threading.Thread(
            target=propagate_context_to_thread(_rpc_poll_loop),
            args=(env, rpc_dir, task_id, tool_call_counter, max_tool_calls, stop_event, rpc_token),
            daemon=True,
        )
        rpc_thread.start()
        env_prefix = (
            f"SPIRITAGENT_RPC_DIR={shlex.quote(rpc_dir)} SPIRITAGENT_RPC_TOKEN={rpc_token} PYTHONDONTWRITEBYTECODE=1"
        )
        if tz := str(cfg_get(load_config(), "terminal", "timezone", default="")).strip():
            env_prefix += f" TZ={shlex.quote(tz)}"
        logger.info("Executing code on %s backend (task %s)...", env_type, task_id)
        # 脚本在终端会话目录运行，spiritagent_tools 随脚本目录进入 sys.path。
        script_result = env.execute(
            f"{env_prefix} python3 {shlex.quote(f'{sandbox_dir}/script.py')}",
            timeout=timeout,
        )
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
        return _error_result(str(exc), tool_call_counter[0], duration)
    finally:
        stop_event.set()
        if rpc_thread is not None:
            rpc_thread.join(timeout=5)
        if sandbox_dir is not None:
            try:
                env.execute(f"rm -rf {shlex.quote(sandbox_dir)}", timeout=15)
            except Exception:
                logger.warning("Failed to clean up remote sandbox %s", sandbox_dir, exc_info=True)
        held_env.close()
    duration = round(time.monotonic() - exec_start, 2)
    stdout_text = script_result.get("output", "")
    if len(stdout_text) > MAX_STDOUT_BYTES:
        omitted = len(stdout_text) - MAX_STDOUT_BYTES
        stdout_text = (
            stdout_text[:_STDOUT_HEAD_BYTES]
            + _truncation_notice(omitted, len(stdout_text))
            + stdout_text[-_STDOUT_TAIL_BYTES:]
        )
    exit_code = script_result.get("returncode", -1)
    return _build_result(
        status={124: "timeout", 130: "interrupted"}.get(exit_code, "success"),
        output=stdout_text,
        stderr="",
        exit_code=exit_code,
        timeout=timeout,
        tool_calls=tool_call_counter[0],
        duration=duration,
    )


def _drain_capped(pipe: IO[bytes], chunks: list[bytes], max_bytes: int) -> None:
    """读尽管道但只保留前 max_bytes 字节；持续读取避免子进程因管道写满而阻塞。"""
    total = 0
    try:
        while data := pipe.read(4096):
            if total < max_bytes:
                chunks.append(data[: max_bytes - total])
            total += len(data)
    except (ValueError, OSError) as e:
        logger.debug("Error reading process output: %s", e, exc_info=True)


def _drain_head_tail(pipe: IO[bytes], head_chunks: list[bytes], tail_chunks: list[bytes], total: list[int]) -> None:
    """读尽管道，保留开头与结尾各一段；结尾段在读完后一次性发布，避免与主线程读取竞争。"""
    head_collected = 0
    tail_buf: deque[bytes] = deque()
    tail_collected = 0
    try:
        while data := pipe.read(4096):
            total[0] += len(data)
            if head_collected < _STDOUT_HEAD_BYTES:
                keep = data[: _STDOUT_HEAD_BYTES - head_collected]
                head_chunks.append(keep)
                head_collected += len(keep)
                if not (data := data[len(keep) :]):
                    continue
            tail_buf.append(data)
            tail_collected += len(data)
            while tail_collected > _STDOUT_TAIL_BYTES and tail_buf:
                tail_collected -= len(tail_buf.popleft())
    except (ValueError, OSError) as e:
        logger.debug("Error reading process output: %s", e, exc_info=True)
    tail_chunks.extend(tail_buf)


def _build_child_env(staging_dir: str, rpc_endpoint: str, rpc_token: str) -> dict[str, str]:
    child_env = _scrub_child_env(os.environ)
    child_env["SPIRITAGENT_RPC_SOCKET"] = rpc_endpoint
    child_env["SPIRITAGENT_RPC_TOKEN"] = rpc_token
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONPATH"] = os.pathsep.join(p for p in (staging_dir, child_env.get("PYTHONPATH", "")) if p)
    child_env["HOME"] = str(get_subprocess_home())
    if tz := str(cfg_get(load_config(), "terminal", "timezone", default="")).strip():
        child_env["TZ"] = tz
    return child_env


def _execute_local(code: str, timeout: int, max_tool_calls: int, mode: str) -> str:
    """本机执行：子进程经 UDS（Windows 为 loopback TCP）RPC 回调父进程工具，首帧用一次性 token 鉴权。"""
    tmpdir = tempfile.mkdtemp(prefix="spiritagent_sandbox_")
    rpc_token = secrets.token_hex(16)
    tool_call_counter = [0]
    exec_start = time.monotonic()
    stop_event = threading.Event()
    sock_path: str | None = None
    server_sock: socket.socket | None = None
    rpc_thread: threading.Thread | None = None
    proc: subprocess.Popen[bytes] | None = None
    try:
        script_path = os.path.join(tmpdir, "script.py")
        with open(os.path.join(tmpdir, "spiritagent_tools.py"), "w", encoding="utf-8") as f:
            f.write(generate_spiritagent_tools_module("uds"))
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)
        if IS_WINDOWS:
            # Windows 的 AF_UNIX 不可靠，改用 loopback TCP。
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.bind(("127.0.0.1", 0))
            host, port = server_sock.getsockname()[:2]
            rpc_endpoint = f"tcp://{host}:{port}"
        else:
            # macOS 默认临时目录路径较长，AF_UNIX 路径上限 104 字节，固定放在 /tmp。
            sock_dir = "/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
            sock_path = rpc_endpoint = os.path.join(sock_dir, f"spiritagent_rpc_{uuid.uuid4().hex}.sock")
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(sock_path)
            os.chmod(sock_path, 0o600)
        server_sock.listen(1)
        rpc_thread = threading.Thread(
            target=propagate_context_to_thread(_rpc_server_loop),
            args=(server_sock, "default", tool_call_counter, max_tool_calls, rpc_token, stop_event),
            daemon=True,
        )
        rpc_thread.start()
        proc = subprocess.Popen(
            [_resolve_child_python(mode), script_path],
            cwd=_resolve_child_cwd(mode, tmpdir),
            env=_build_child_env(tmpdir, rpc_endpoint, rpc_token),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=not IS_WINDOWS,
            creationflags=CREATE_NO_WINDOW,
        )
        stdout_head_chunks: list[bytes] = []
        stdout_tail_chunks: list[bytes] = []
        stdout_total_bytes = [0]
        stderr_chunks: list[bytes] = []
        stdout_reader = threading.Thread(
            target=_drain_head_tail,
            args=(proc.stdout, stdout_head_chunks, stdout_tail_chunks, stdout_total_bytes),
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=_drain_capped,
            args=(proc.stderr, stderr_chunks, MAX_STDERR_BYTES),
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader.start()
        deadline = time.monotonic() + timeout
        status = "success"
        while proc.poll() is None:
            if is_interrupted():
                status = "interrupted"
                break
            if time.monotonic() > deadline:
                status = "timeout"
                break
            time.sleep(0.2)
        if status != "success":
            _kill_process_group(proc)
        stdout_reader.join(timeout=3)
        stderr_reader.join(timeout=3)
        stdout_head = b"".join(stdout_head_chunks).decode("utf-8", errors="replace")
        stdout_tail = b"".join(stdout_tail_chunks).decode("utf-8", errors="replace")
        total_stdout = stdout_total_bytes[0]
        if total_stdout > MAX_STDOUT_BYTES and stdout_tail:
            omitted = total_stdout - len(stdout_head) - len(stdout_tail)
            stdout_text = stdout_head + _truncation_notice(omitted, total_stdout) + stdout_tail
        else:
            stdout_text = stdout_head + stdout_tail
        return _build_result(
            status=status,
            output=stdout_text,
            stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
            timeout=timeout,
            tool_calls=tool_call_counter[0],
            duration=round(time.monotonic() - exec_start, 2),
        )
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
        return _error_result(str(exc), tool_call_counter[0], duration)
    finally:
        stop_event.set()
        if proc is not None and proc.poll() is None:
            _kill_process_group(proc)
        if rpc_thread is not None:
            rpc_thread.join(timeout=3)
        if server_sock is not None:
            try:
                server_sock.close()
            except OSError as e:
                logger.debug("Server socket close error: %s", e)
        if sock_path is not None:
            try:
                os.unlink(sock_path)
            except OSError as e:
                logger.debug("RPC socket unlink error: %s", e)
        shutil.rmtree(tmpdir, ignore_errors=True)


def execute_code(code: str) -> str:
    """执行 sandbox 子进程: 本机用 UDS/TCP RPC, SSH 后端委派 ``_execute_remote``。"""
    if not code or not code.strip():
        return tool_error("No code provided.")
    cfg = _code_execution_config()
    timeout = cfg_int(cfg, "timeout", DEFAULT_TIMEOUT)
    max_tool_calls = cfg_int(cfg, "max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    if get_env_config()["env_type"] != "local":
        return _execute_remote(code, timeout, max_tool_calls)
    return _execute_local(code, timeout, max_tool_calls, _get_execution_mode(cfg))


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """终止沙箱子进程及其进程树；取消与超时都升级到强杀，返回时脚本不再运行。"""
    try:
        terminate_tree(proc, graceful_timeout=1.0, force_timeout=5.0, escalate=True)
    except Exception as e:
        logger.warning("execute_code: terminate_tree failed, killing the script process only: %s", e, exc_info=True)
        with contextlib.suppress(OSError):
            proc.kill()


def _code_execution_config() -> dict[str, Any]:
    cfg = load_config().get("code_execution")
    return cfg if isinstance(cfg, dict) else {}


def _get_execution_mode(cfg: dict[str, Any]) -> str:
    """读取合法的 ``code_execution.mode``, 非法值降级为默认。"""
    mode = cfg_str(cfg, "mode", DEFAULT_EXECUTION_MODE).lower()
    if mode in EXECUTION_MODES:
        return mode
    logger.warning(
        "Ignoring code_execution.mode=%r (expected one of %s), falling back to %r",
        mode,
        EXECUTION_MODES,
        DEFAULT_EXECUTION_MODE,
    )
    return DEFAULT_EXECUTION_MODE


@functools.lru_cache(maxsize=32)
def _is_usable_python(python_path: str) -> bool:
    """探测解释器能否运行且版本 >= 3.8。"""
    try:
        result = subprocess.run(
            [python_path, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"],
            timeout=5,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _resolve_child_python(mode: str) -> str:
    """解析沙箱子进程用的 Python 解释器(优先 installer 装的 uv-managed venv, 再看 VIRTUAL_ENV/CONDA_PREFIX)。"""
    if mode != "project":
        return sys.executable
    if (managed := find_python()) and _is_usable_python(managed):
        return managed
    exe_names, subdir = (("python.exe", "python3.exe"), "Scripts") if IS_WINDOWS else (("python", "python3"), "bin")
    for var in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        if not (root := os.environ.get(var, "").strip()):
            continue
        for exe in exe_names:
            candidate = os.path.join(root, subdir, exe)
            if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
                continue
            if _is_usable_python(candidate):
                return candidate
            logger.info(
                "execute_code: skipping %s=%s (Python version < 3.8 or broken). Using sys.executable instead.",
                var,
                candidate,
            )
            return sys.executable
    return sys.executable


def _resolve_child_cwd(mode: str, staging_dir: str) -> str:
    """project 模式在终端当前工作目录运行（随终端 cd 变化，目录不存在时回落 staging），strict 模式直接用 staging。"""
    if mode != "project":
        return staging_dir
    with use_environment("default") as env:
        cwd = env.cwd
    return cwd if os.path.isdir(cwd) else staging_dir


EXECUTE_CODE_SCHEMA = {
    "name": "execute_code",
    "description": (
        "Run a Python script that can call SpiritAgent tools programmatically. Use this when "
        "you need 3+ tool calls with processing logic between them, need to filter/reduce "
        "large tool outputs before they enter your context, need conditional branching "
        "(if X then Y else Z), or need to loop (fetch N pages, process N files, retry on "
        "failure when safe). Tool availability and authorization remain the same as for direct calls; "
        "the imported helper names do not unlock disabled tools.\n\n"
        "Use normal tool calls instead when: single tool call with no processing, you need "
        "to see the full result and apply complex reasoning, or the task requires interactive "
        "user input.\n\n"
        "Available via `from spiritagent_tools import ...`:\n\n"
        "  read_file(path: str, offset: int = 1, limit: int = 500) -> dict\n"
        '    Lines are 1-indexed. Returns {"content": "...", "total_lines": N}; '
        'each content line is prefixed with "LINE_NUM|".\n'
        "  write_file(path: str, content: str) -> dict\n"
        "    Always overwrites the entire file.\n"
        '  search_files(pattern: str, target="content", path=".", file_glob=None, '
        "limit=50) -> dict\n"
        '    target: "content" (search inside files) or "files" (find files by name). '
        'Returns {"total_count": N} plus "matches" (content search, items with path/line/content) '
        'or "files" (name search); the list key is omitted when nothing matches.\n'
        "  patch(path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict\n"
        "    Replaces old_string with new_string in the file.\n"
        "  terminal(command: str, timeout=None, workdir=None) -> dict\n"
        '    Foreground only (no background/pty). Returns {"output": "...", "exit_code": N}\n\n'
        'A failed tool call returns a dict with an "error" key instead of raising.\n\n'
        "Limits: 5-minute timeout, 50KB stdout cap, max 50 tool calls per script.\n\n"
        "Scripts start in the terminal working directory, so relative paths resolve as in terminal(). "
        "Locally they run on a bundled Python interpreter: packages installed for other interpreters "
        "may be missing, so use terminal() to run project tooling.\n\n"
        "Print your final result to stdout. Use Python stdlib (json, re, math, csv, "
        "datetime, collections, etc.) for processing between tool calls.\n\n"
        "Also available (no import needed — built into spiritagent_tools):\n"
        "  json_parse(text: str) — json.loads with strict=False; use for terminal() output "
        "with control chars\n"
        "  shell_quote(s: str) — shlex.quote(); use when interpolating dynamic strings into "
        "shell commands\n"
        "  retry(fn, max_attempts=3, delay=2) — retry raised exceptions with exponential backoff; "
        "it does not inspect tool error results or determine whether retrying is safe. Use only for "
        "read-only or otherwise verified repeatable work. After an unknown outcome, check the original "
        "operation before repeating a write, send, purchase, or submission."
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
