"""为独立脚本生成工具桩和鉴权传输客户端。"""

from typing import Literal

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
