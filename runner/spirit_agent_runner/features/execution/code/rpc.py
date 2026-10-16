"""代码子进程的父端 RPC，鉴权、调用预算和允许工具限制集中于此。"""

import base64
import contextlib
import json
import logging
import secrets
import select
import shlex
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from spirit_agent_runner.contracts.context import current_context, invocation_scope
from spirit_agent_runner.contracts.thread_context import propagate_context_to_thread
from spirit_agent_runner.contracts.tooling import ToolError, tool_error
from spirit_agent_runner.features.execution.api import BaseEnvironment, invoke_tool
from spirit_agent_runner.features.execution.code.child_api import SANDBOX_ALLOWED_TOOLS

logger = logging.getLogger(__name__)


class _ScriptCancellation(threading.Event):
    def __init__(self, parent: threading.Event) -> None:
        super().__init__()
        self._parent = parent

    def is_set(self) -> bool:
        return super().is_set() or self._parent.is_set()


class RpcWorker:
    """脚本拥有 RPC 工作线程；脚本结束须中断子调用并等待收尾。"""

    def __init__(self, target: Callable[..., None], args: tuple[object, ...]) -> None:
        self._cancel = _ScriptCancellation(current_context().cancel_event)
        context = replace(current_context(), cancel_event=self._cancel)

        def run() -> None:
            with invocation_scope(context):
                target(*args)

        self._thread = threading.Thread(target=propagate_context_to_thread(run), daemon=True, name="code-rpc")
        self._started = False

    def start(self) -> None:
        self._thread.start()
        self._started = True

    def close(self) -> None:
        self._cancel.set()
        if self._started:
            self._thread.join()


_RPC_POLL_SECONDS = 0.2


_TERMINAL_BLOCKED_PARAMS = {"background", "pty"}


def _token_matches(candidate: object, expected_token: str) -> bool:
    return isinstance(candidate, str) and secrets.compare_digest(candidate.encode(), expected_token.encode())


def _dispatch_sandbox_call(
    request: object,
    tool_call_counter: list[int],
    max_tool_calls: int,
) -> Any:
    """执行器统一检查可用性；RPC 另限制脚本可用工具、参数和调用预算。"""
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
    try:
        result = invoke_tool(tool_name, tool_args)
    except ToolError as error:
        return tool_error(str(error))
    return result


def _read_conn_line(
    conn: socket.socket,
    buf: bytes,
    stop_event: threading.Event,
) -> tuple[bytes | None, bytes]:
    """鉴权首帧共享五秒预算，分段接收仍须响应脚本关闭。"""
    deadline = time.monotonic() + 5.0
    while b"\n" not in buf:
        remaining = deadline - time.monotonic()
        if stop_event.is_set() or remaining <= 0:
            return None, buf
        conn.settimeout(min(_RPC_POLL_SECONDS, remaining))
        try:
            chunk = conn.recv(65536)
        except TimeoutError:
            continue
        except OSError:
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


def run_socket_rpc(
    server_sock: socket.socket,
    tool_call_counter: list[int],
    max_tool_calls: int,
    expected_token: str,
    stop_event: threading.Event,
) -> None:
    """本机子进程的父端 RPC：只接受首帧携带本次 token 的一条连接，串行派发工具调用，直到脚本结束。"""
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
            # Windows 用 loopback TCP；首帧鉴权失败立即断开。
            line, buf = _read_conn_line(candidate, b"", stop_event)
            if line is not None and _is_auth_frame(line, expected_token):
                conn = candidate
            else:
                logger.debug("execute_code RPC: rejected unauthenticated connection")
                candidate.close()
        # 连接保持打开以便大结果；select 轮询停止信号。
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
                    result = _dispatch_sandbox_call(request, tool_call_counter, max_tool_calls)
                conn.sendall((json.dumps(result, ensure_ascii=False) + "\n").encode())
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


def ship_file_to_remote(env: BaseEnvironment, remote_path: str, content: str) -> None:
    """把文本写到远端 remote_path；经 base64 + stdin 传输，避开转义与命令行长度限制。"""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    result = env.execute(f"base64 -d > {shlex.quote(remote_path)}", stdin_data=encoded, timeout=30)
    if result.get("returncode") != 0:
        raise RuntimeError(f"Failed to write {remote_path}: {result.get('output', '').strip()}")


def _serve_remote_request(
    env: BaseEnvironment,
    rpc_dir: str,
    req_file: str,
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
    tool_result = _dispatch_sandbox_call(request, tool_call_counter, max_tool_calls)
    quoted_res_file = shlex.quote(f"{rpc_dir}/res_{seq:06d}")
    env.execute(
        f"base64 -d > {quoted_res_file}.tmp && mv {quoted_res_file}.tmp {quoted_res_file}",
        stdin_data=base64.b64encode(json.dumps(tool_result, ensure_ascii=False).encode("utf-8")).decode("ascii"),
        timeout=60,
    )


def run_file_rpc(
    env: BaseEnvironment,
    rpc_dir: str,
    tool_call_counter: list[int],
    max_tool_calls: int,
    stop_event: threading.Event,
    expected_token: str,
) -> None:
    """轮询远端脚本的 RPC 请求文件，派发工具调用并写回响应文件。"""
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
                    tool_call_counter,
                    max_tool_calls,
                    expected_token,
                )
        except Exception as e:
            if not stop_event.is_set():
                logger.debug("RPC poll error: %s", e, exc_info=True)
        stop_event.wait(poll_interval)
