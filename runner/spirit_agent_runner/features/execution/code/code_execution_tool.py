import contextlib
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
from typing import Any

from spirit_agent_runner.contracts.config import cfg_get, cfg_int, load_config
from spirit_agent_runner.contracts.interrupt import is_interrupted
from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.execution.api import BaseEnvironment, current_task_id, get_env_config, use_environment
from spirit_agent_runner.features.execution.code.child_api import generate_spiritagent_tools_module
from spirit_agent_runner.features.execution.code.output import (
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    STDOUT_HEAD_BYTES,
    STDOUT_TAIL_BYTES,
    build_result,
    drain_capped,
    drain_head_tail,
    error_result,
    truncation_notice,
)
from spirit_agent_runner.features.execution.code.rpc import RpcWorker, run_file_rpc, run_socket_rpc, ship_file_to_remote
from spirit_agent_runner.features.execution.code.selection import (
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_TIMEOUT,
    build_child_env,
    code_execution_config,
    execution_mode,
    resolve_child_cwd,
    resolve_child_python,
)
from spirit_agent_runner.infrastructure.constants import CREATE_NO_WINDOW, IS_WINDOWS
from spirit_agent_runner.infrastructure.process_tree import terminate_tree

logger = logging.getLogger(__name__)


def _execute_remote(code: str, timeout: int, max_tool_calls: int) -> dict[str, Any]:
    """在 SSH 沙箱后端里执行 ``code``; 通过文件 RPC 转发工具调用。"""
    task_id = current_task_id()
    tool_call_counter = [0]
    exec_start = time.monotonic()
    stop_event = threading.Event()
    rpc_thread: RpcWorker | None = None
    env: BaseEnvironment | None = None
    sandbox_dir: str | None = None
    # 持有环境直到远端沙箱清理完。
    held_env = contextlib.ExitStack()
    try:
        env = held_env.enter_context(use_environment(task_id))
        env_type = env.env_type
        py_check = env.execute("command -v python3 >/dev/null 2>&1 && echo OK", timeout=15)
        if "OK" not in py_check.get("output", ""):
            return error_result(
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
        # 0700 且不带 -p：防其他用户读写或抢占目录名。
        mkdir_result = env.execute(
            f"mkdir -m 700 {shlex.quote(candidate_dir)} && mkdir {shlex.quote(rpc_dir)}",
            timeout=10,
        )
        if mkdir_result.get("returncode") != 0:
            raise RuntimeError(f"Failed to create remote sandbox directory: {mkdir_result.get('output', '').strip()}")
        sandbox_dir = candidate_dir
        ship_file_to_remote(env, f"{sandbox_dir}/spiritagent_tools.py", generate_spiritagent_tools_module("file"))
        ship_file_to_remote(env, f"{sandbox_dir}/script.py", code)
        rpc_thread = RpcWorker(
            run_file_rpc,
            (env, rpc_dir, task_id, tool_call_counter, max_tool_calls, stop_event, rpc_token),
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
        return error_result(str(exc), tool_call_counter[0], duration)
    finally:
        stop_event.set()
        if rpc_thread is not None:
            rpc_thread.close()
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
            stdout_text[:STDOUT_HEAD_BYTES]
            + truncation_notice(omitted, len(stdout_text))
            + stdout_text[-STDOUT_TAIL_BYTES:]
        )
    exit_code = script_result.get("returncode", -1)
    return build_result(
        status={124: "timeout", 130: "interrupted"}.get(exit_code, "success"),
        output=stdout_text,
        stderr="",
        exit_code=exit_code,
        timeout=timeout,
        tool_calls=tool_call_counter[0],
        duration=duration,
    )


def _execute_local(code: str, timeout: int, max_tool_calls: int, mode: str) -> dict[str, Any]:
    """本机执行：子进程经 UDS（Windows 为 loopback TCP）RPC 回调父进程工具，首帧用一次性 token 鉴权。"""
    tmpdir = tempfile.mkdtemp(prefix="spiritagent_sandbox_")
    rpc_token = secrets.token_hex(16)
    tool_call_counter = [0]
    exec_start = time.monotonic()
    stop_event = threading.Event()
    output_stop = threading.Event()
    sock_path: str | None = None
    server_sock: socket.socket | None = None
    rpc_thread: RpcWorker | None = None
    proc: subprocess.Popen[bytes] | None = None
    output_readers: list[threading.Thread] = []
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
            # AF_UNIX 上限 104 字节，macOS 固定用 /tmp。
            sock_dir = "/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
            sock_path = rpc_endpoint = os.path.join(sock_dir, f"spiritagent_rpc_{uuid.uuid4().hex}.sock")
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(sock_path)
            os.chmod(sock_path, 0o600)
        server_sock.listen(1)
        rpc_thread = RpcWorker(
            run_socket_rpc,
            (server_sock, current_task_id(), tool_call_counter, max_tool_calls, rpc_token, stop_event),
        )
        rpc_thread.start()
        proc = subprocess.Popen(
            [resolve_child_python(mode), script_path],
            cwd=resolve_child_cwd(mode, tmpdir),
            env=build_child_env(tmpdir, rpc_endpoint, rpc_token),
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
            target=drain_head_tail,
            args=(proc.stdout, stdout_head_chunks, stdout_tail_chunks, stdout_total_bytes, output_stop),
            daemon=True,
            name="code-stdout",
        )
        stderr_reader = threading.Thread(
            target=drain_capped,
            args=(proc.stderr, stderr_chunks, MAX_STDERR_BYTES, output_stop),
            daemon=True,
            name="code-stderr",
        )
        for reader in (stdout_reader, stderr_reader):
            reader.start()
            output_readers.append(reader)
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
        output_stop.set()
        for reader in output_readers:
            reader.join()
        stdout_head = b"".join(stdout_head_chunks).decode("utf-8", errors="replace")
        stdout_tail = b"".join(stdout_tail_chunks).decode("utf-8", errors="replace")
        total_stdout = stdout_total_bytes[0]
        if total_stdout > MAX_STDOUT_BYTES and stdout_tail:
            omitted = total_stdout - len(stdout_head) - len(stdout_tail)
            stdout_text = stdout_head + truncation_notice(omitted, total_stdout) + stdout_tail
        else:
            stdout_text = stdout_head + stdout_tail
        return build_result(
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
        return error_result(str(exc), tool_call_counter[0], duration)
    finally:
        stop_event.set()
        if proc is not None and proc.poll() is None:
            _kill_process_group(proc)
        output_stop.set()
        for reader in output_readers:
            reader.join()
        if proc is not None:
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
        if rpc_thread is not None:
            rpc_thread.close()
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


def execute_code(code: str) -> dict[str, Any]:
    """执行 sandbox 子进程: 本机用 UDS/TCP RPC, SSH 后端委派 ``_execute_remote``。"""
    if not code or not code.strip():
        return {"error": "No code provided."}
    cfg = code_execution_config()
    timeout = cfg_int(cfg, "timeout", DEFAULT_TIMEOUT)
    max_tool_calls = cfg_int(cfg, "max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    if get_env_config()["env_type"] != "local":
        return _execute_remote(code, timeout, max_tool_calls)
    return _execute_local(code, timeout, max_tool_calls, execution_mode(cfg))


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """终止沙箱子进程及其进程树；取消与超时都升级到强杀，返回时脚本不再运行。"""
    try:
        terminate_tree(proc, graceful_timeout=1.0, force_timeout=5.0, escalate=True)
    except Exception as e:
        logger.warning("execute_code: terminate_tree failed, killing the script process only: %s", e, exc_info=True)
        with contextlib.suppress(OSError):
            proc.kill()


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
        "  patch(path: str = None, old_string: str = None, new_string: str = None, "
        'replace_all: bool = False, mode: str = "replace", patch: str = None) -> dict\n'
        '    mode="replace" requires path, old_string and new_string and performs a targeted replacement; '
        'mode="patch" requires patch containing a V4A multi-file patch.\n'
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
        "additionalProperties": False,
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


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="execute_code",
            toolset_id="code_execution",
            schema=EXECUTE_CODE_SCHEMA,
            handler=lambda _context, args: execute_code(code=args.get("code", "")),
            check_fn=None,
            is_async=False,
        ),
    ]
