import atexit
import codecs
import io
import json
import logging
import os
import shlex
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from envs import BaseEnvironment, get_env_config, local_run_env, register_active_process_checker
from utils import (
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    clean_output,
    find_bash,
    is_interrupted,
    resolve_safe_cwd,
    terminate_tree,
)

from ..registry import registry, tool_error

if IS_WINDOWS:
    from winpty import PtyProcess
else:
    from ptyprocess import PtyProcess

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 200_000  # 滚动输出缓冲上限
FINISHED_TTL_SECONDS = 1800  # 已结束的进程保留 30 分钟
MAX_PROCESSES = 64  # 同时跟踪的进程上限，超出时淘汰最早结束的

_STDIN_UNAVAILABLE = "Process stdin is not available (remote process, or stdin was closed)"


@dataclass
class ProcessSession:
    """一个被跟踪的后台进程及其滚动输出。"""

    id: str  # "proc_xxxxxxxxxxxx"
    command: str
    task_id: str
    cwd: str
    started_at: float
    pid: int | None = None  # 本机为宿主 PID；SSH 为远端包裹子 shell 的 PID
    process: subprocess.Popen | None = None  # 本机管道模式
    env_ref: BaseEnvironment | None = None  # SSH 模式
    finished_at: float = 0.0  # 已结束 session 的 TTL 起点
    exited: bool = False
    exit_code: int | None = None  # 未结束或无法得知时为 None
    output_buffer: str = ""  # 最近 MAX_OUTPUT_CHARS 个字符
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _reader_thread: threading.Thread | None = field(default=None, repr=False)
    _pty: Any = field(default=None, repr=False)  # ptyprocess / pywinpty 句柄（本机 PTY 模式）

    def append_output(self, text: str) -> None:
        with self._lock:
            self.output_buffer = (self.output_buffer + text)[-MAX_OUTPUT_CHARS:]

    def output_tail(self, limit: int) -> str:
        # 多取一段再清洗脱敏、最后截取：直接在凭据中间截断时，残片不再能被识别。
        with self._lock:
            window = self.output_buffer[-(limit + 8192) :]
        return clean_output(window)[-limit:] if window else ""

    def uptime_seconds(self) -> int:
        return int((self.finished_at or time.time()) - self.started_at)


class PTYBufferFull(OSError):
    """PTY 写入缓冲区已满且在背压超时内未能消费。"""


class ProcessRegistry:
    """运行中 / 已结束后台进程的内存注册表，线程安全。"""

    _SHELL_NOISE_SUBSTRINGS = (
        "bash: cannot set terminal process group",
        "bash: no job control in this shell",
        "no job control in this shell",
        "cannot set terminal process group",
        "tcsetattr: Inappropriate ioctl for device",
    )

    def __init__(self) -> None:
        self._running: dict[str, ProcessSession] = {}
        self._finished: dict[str, ProcessSession] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _clean_shell_noise(text: str) -> str:
        """剥掉输出开头几行的 shell 启动告警（交互式 shell 标志等）。"""
        lines = text.split("\n")
        while lines and any(noise in lines[0] for noise in ProcessRegistry._SHELL_NOISE_SUBSTRINGS):
            lines.pop(0)
        return "\n".join(lines)

    # ----- Spawn -----
    def _track(self, session: ProcessSession, reader: Callable[[ProcessSession], None], name: str) -> None:
        """先登记再启动读线程：读线程可能立即结束并把 session 移入 finished。"""
        with self._lock:
            self._prune_if_needed()
            self._running[session.id] = session
        thread = threading.Thread(target=reader, args=(session,), daemon=True, name=f"{name}-{session.id}")
        session._reader_thread = thread
        try:
            thread.start()
        except BaseException:
            # 读线程起不来就无人回收：终止进程后上抛，避免遗留不可见的进程。
            try:
                self._terminate(session)
            except Exception as e:
                logger.warning("Could not terminate process %s after reader start failure: %s", session.id, e)
            self._mark_exited(session, None)
            raise

    def spawn_local(self, command: str, cwd: str, task_id: str, use_pty: bool = False) -> ProcessSession:
        """在本机派生后台进程；``use_pty=True`` 在伪终端中运行，供交互式 CLI 使用。"""
        session = ProcessSession(
            id=f"proc_{uuid.uuid4().hex[:12]}",
            command=command,
            task_id=task_id,
            cwd=resolve_safe_cwd(cwd),
            started_at=time.time(),
        )
        argv = [find_bash(), "-lic", f"set +m; {command}"]
        # 强制无缓冲：tqdm / datasets 等库在非 TTY stdout 上会缓冲，poll 看不到进度。
        run_env = local_run_env() | {"PYTHONUNBUFFERED": "1"}
        if use_pty:
            pty_proc = PtyProcess.spawn(argv, cwd=session.cwd, env=run_env, dimensions=(30, 120))
            session.pid = pty_proc.pid
            session._pty = pty_proc
            self._track(session, self._pty_reader_loop, "proc-pty-reader")
            return session
        proc = subprocess.Popen(
            argv,
            text=True,
            cwd=session.cwd,
            env=run_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            # POSIX 下独立进程组，kill 时整组终止；Windows 忽略该参数。
            start_new_session=True,
            creationflags=CREATE_NO_WINDOW,
        )
        session.process = proc
        session.pid = proc.pid
        self._track(session, self._reader_loop, "proc-reader")
        return session

    def spawn_via_env(self, env: BaseEnvironment, command: str, cwd: str, task_id: str) -> ProcessSession:
        """经 SSH 环境派生后台进程：远端 nohup 运行，PID、输出与退出码写入远端临时文件，读线程轮询。

        不支持实时 stdout 管道与 stdin 输入；启动失败时抛 RuntimeError。
        """
        session = ProcessSession(
            id=f"proc_{uuid.uuid4().hex[:12]}",
            command=command,
            task_id=task_id,
            cwd=cwd,
            started_at=time.time(),
            env_ref=env,
        )
        temp_dir = env.get_temp_dir().rstrip("/") or "/"
        log_path = f"{temp_dir}/spiritagent_bg_{session.id}.log"
        pid_path = f"{temp_dir}/spiritagent_bg_{session.id}.pid"
        exit_path = f"{temp_dir}/spiritagent_bg_{session.id}.exit"
        quoted_log_path = shlex.quote(log_path)
        quoted_pid_path = shlex.quote(pid_path)
        bg_command = (
            f"mkdir -p {shlex.quote(temp_dir)} && "
            f"( nohup bash -lc {shlex.quote(command)} > {quoted_log_path} 2>&1; "
            f"rc=$?; printf '%s\\n' \"$rc\" > {shlex.quote(exit_path)} ) & "
            f"echo $! > {quoted_pid_path} && cat {quoted_pid_path}"
        )
        result = env.execute(bg_command, cwd=cwd, timeout=10, rewrite_compound_background=False)
        output = result.get("output", "").strip()
        session.pid = next((int(line) for line in map(str.strip, output.splitlines()) if line.isdigit()), None)
        if session.pid is None:
            # 包裹命令没产出 PID（语法错误 / 重定向失败）：按启动失败处理，不伪装成运行中的 session。
            raise RuntimeError(f"remote start failed (exit code {result.get('returncode')}): {output[-500:]}")
        self._track(
            session,
            lambda s: self._env_poller_loop(s, env, log_path, pid_path, exit_path),
            "proc-poller",
        )
        return session

    # ----- Reader / Poller Threads -----
    def _reader_loop(self, session: ProcessSession) -> None:
        """读线程：直接读管道 fd，有数据即追加（文本流的 read(n) 要攒满 n 个字符或遇到 EOF 才返回）。"""
        proc = session.process
        decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True)
        first_chunk = True
        try:
            fd = proc.stdout.fileno()
            while chunk := os.read(fd, 4096):
                if not (text := decoder.decode(chunk)):
                    continue
                if first_chunk:
                    text = self._clean_shell_noise(text)
                    first_chunk = False
                session.append_output(text)
            if tail := decoder.decode(b"", final=True):
                session.append_output(tail)
        except (OSError, ValueError) as e:
            logger.debug("Process stdout reader for %s ended: %s", session.id, e)
        finally:
            # EOF 之后回收子进程；直接子进程可能关闭 stdout 后继续运行，退出前 session 仍算运行中。
            self._mark_exited(session, proc.wait())

    def _env_poller_loop(
        self,
        session: ProcessSession,
        env: BaseEnvironment,
        log_path: str,
        pid_path: str,
        exit_path: str,
    ) -> None:
        """轮询线程（SSH）：每 2 秒读取远端日志与存活状态。"""
        quoted_log_path = shlex.quote(log_path)
        quoted_pid_path = shlex.quote(pid_path)
        quoted_exit_path = shlex.quote(exit_path)
        while not session.exited:
            time.sleep(2)
            try:
                result = env.execute(f"cat {quoted_log_path} 2>/dev/null", timeout=10)
                # 非 0（含 ssh 连接失败 255）时输出是错误信息而不是日志，不能覆盖缓冲。
                if result["returncode"] == 0 and result["output"]:
                    with session._lock:
                        session.output_buffer = result["output"][-MAX_OUTPUT_CHARS:]
                check = env.execute(f'kill -0 "$(cat {quoted_pid_path} 2>/dev/null)" 2>/dev/null', timeout=5)
                # kill -0：0 存活，1 已退出；其他（ssh 失败、超时、取消）状态未知，下一轮重试。
                if check["returncode"] != 1:
                    continue
                exit_result = env.execute(f"cat {quoted_exit_path} 2>/dev/null", timeout=5)
                try:
                    exit_code = int(exit_result["output"].split()[-1]) if exit_result["returncode"] == 0 else None
                except (ValueError, IndexError):
                    exit_code = None
                self._mark_exited(session, exit_code)
                return
            except Exception as e:
                logger.warning(
                    "Polling remote process %s failed; marking it exited with unknown status: %s",
                    session.id,
                    e,
                )
                self._mark_exited(session, None)
                return

    def _pty_reader_loop(self, session: ProcessSession) -> None:
        """读线程：从 PTY 读输出。"""
        pty = session._pty
        try:
            while pty.isalive():
                chunk = pty.read(4096)
                if chunk:
                    text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
                    session.append_output(text)
        except EOFError:
            pass
        except Exception as e:
            logger.debug("PTY reader for %s ended: %s", session.id, e)
        try:
            pty.wait()
        except Exception as e:
            logger.debug("PTY wait failed for %s: %s", session.id, e)
        self._mark_exited(session, pty.exitstatus)

    def _mark_exited(self, session: ProcessSession, exit_code: int | None) -> None:
        """标记退出并移入 finished；幂等，先记录到的退出码不被 None 覆盖。"""
        with session._lock:
            session.exited = True
            if session.exit_code is None:
                session.exit_code = exit_code
        with self._lock:
            self._running.pop(session.id, None)
            if not session.finished_at:
                session.finished_at = time.time()
            self._finished[session.id] = session

    # ----- Query Methods -----
    def get(self, session_id: str) -> ProcessSession | None:
        """根据 ID 取 session（运行中 / 已结束均可）。"""
        with self._lock:
            return self._running.get(session_id) or self._finished.get(session_id)

    def _reconcile_local_exit(self, session: ProcessSession) -> None:
        """直接子进程已退出但读线程仍未见 EOF 时（后代进程持有 stdout 写端），按子进程退出码标记结束。

        读线程直接读 fd，已写入管道的输出会被及时读走；这里只短暂等待它收尾，不从管道抢读。
        """
        if session.exited or (proc := session.process) is None or (rc := proc.poll()) is None:
            return
        if session._reader_thread is not None:
            session._reader_thread.join(timeout=0.2)
        if session.exited:
            return
        logger.info(
            "Process %s: direct child exited with code %s while its output pipe is still held open; marking exited.",
            session.id,
            rc,
        )
        self._mark_exited(session, rc)

    def poll(self, session_id: str) -> dict:
        """查询后台进程的状态与最近输出。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        self._reconcile_local_exit(session)
        result = {
            "session_id": session.id,
            "command": session.command,
            "status": "exited" if session.exited else "running",
            "pid": session.pid,
            "uptime_seconds": session.uptime_seconds(),
            "output_preview": session.output_tail(1000),
        }
        if session.exited:
            result["exit_code"] = session.exit_code
        return result

    def read_log(self, session_id: str, offset: int = 0, limit: int = 200) -> dict:
        """读取完整输出日志，按行分页（默认返回末尾 limit 行）。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        with session._lock:
            buffer = session.output_buffer
        lines = clean_output(buffer).splitlines()
        selected = lines[-limit:] if offset == 0 and limit > 0 else lines[offset : offset + limit]
        joined = "\n".join(selected)
        # 按结果大小上限兜底截断：单行很长的日志乘以默认行数可能超限。
        max_chars = registry.get_max_result_size()
        result = {
            "session_id": session.id,
            "status": "exited" if session.exited else "running",
            "output": joined[:max_chars],
            "total_lines": len(lines),
            "showing": f"{len(selected)} lines",
        }
        if len(joined) > max_chars:
            result["truncated"] = True
            result["hint"] = f"Output exceeded {max_chars} chars; truncated. Use offset to page through earlier lines."
        return result

    def wait(self, session_id: str, timeout: int | None = None) -> dict:
        """阻塞到进程退出、超时或调用被取消；等待上限为 terminal.timeout，防止模型传入过大值。"""
        max_timeout = get_env_config()["timeout"]
        timeout_note = None
        if timeout and timeout > max_timeout:
            timeout_note = f"Requested wait of {timeout}s was clamped to configured limit of {max_timeout}s"
            effective_timeout = max_timeout
        else:
            effective_timeout = timeout or max_timeout
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        deadline = time.monotonic() + effective_timeout
        while True:
            self._reconcile_local_exit(session)
            if session.exited:
                result = {"status": "exited", "exit_code": session.exit_code, "output": session.output_tail(2000)}
                break
            if is_interrupted():
                result = {
                    "status": "interrupted",
                    "output": session.output_tail(1000),
                    "note": "Caller cancelled the wait",
                }
                break
            if time.monotonic() >= deadline:
                result = {"status": "timeout", "output": session.output_tail(1000)}
                timeout_note = timeout_note or f"Waited {effective_timeout}s, process still running"
                break
            time.sleep(1)
        if timeout_note:
            result["timeout_note"] = timeout_note
        return result

    def _terminate(self, session: ProcessSession) -> None:
        """终止 session 对应的进程树；失败时抛出。"""
        if session._pty is not None:
            try:
                session._pty.terminate(force=True)
            except Exception as e:
                logger.debug("PTY terminate failed for %s (%s); killing process tree", session.id, e)
                terminate_tree(session._pty.pid, graceful_timeout=0.5, force_timeout=1.0, escalate=True)
        elif session.process is not None:
            terminate_tree(session.process, graceful_timeout=0.5, force_timeout=1.0, escalate=True)
        elif session.env_ref is not None and session.pid is not None:
            # 记录的 pid 是包裹子 shell（$!），实际命令是其子进程：先 TERM，仍存活再 KILL。
            env = session.env_ref
            qpid = shlex.quote(str(session.pid))
            env.execute(f"pkill -TERM -P {qpid} 2>/dev/null; kill {qpid} 2>/dev/null", timeout=5)
            alive = env.execute(f"kill -0 {qpid} 2>/dev/null || pgrep -P {qpid} >/dev/null 2>&1", timeout=5)
            if alive["returncode"] == 0:
                env.execute(f"pkill -KILL -P {qpid} 2>/dev/null; kill -9 {qpid} 2>/dev/null", timeout=5)

    def kill_process(self, session_id: str) -> dict:
        """终止后台进程（本机 PTY / 本机管道 / SSH）。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "exit_code": session.exit_code}
        try:
            self._terminate(session)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        # 只记录真实观察到的退出码：taskkill /F 与远端 kill 都没有可映射的信号退出码。
        self._mark_exited(session, session.process.poll() if session.process is not None else None)
        return {"status": "killed", "session_id": session.id}

    def write_stdin(self, session_id: str, data: str) -> dict:
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "error": "Process has already finished"}
        chunk_size = 4096
        if session._pty is not None:
            try:
                pty_proc = session._pty
                deadline = time.monotonic() + 5.0
                if IS_WINDOWS:
                    for i in range(0, len(data), chunk_size):
                        chunk = data[i : i + chunk_size]
                        retries = 2
                        while retries > 0:
                            if time.monotonic() > deadline:
                                raise PTYBufferFull("PTY write timed out due to buffer backpressure")
                            try:
                                pty_proc.write(chunk)
                                time.sleep(0.001)
                                break
                            except (OSError, ValueError) as exc:
                                retries -= 1
                                if retries == 0:
                                    raise PTYBufferFull(f"PTY buffer full: {exc}") from exc
                                time.sleep(0.01)
                else:
                    bytes_data = data.encode("utf-8")
                    for i in range(0, len(bytes_data), chunk_size):
                        chunk = bytes_data[i : i + chunk_size]
                        retries = 3
                        while retries > 0:
                            if time.monotonic() > deadline:
                                raise PTYBufferFull("PTY write timed out due to buffer backpressure")
                            try:
                                pty_proc.write(chunk)
                                time.sleep(0.001)
                                break
                            except (BlockingIOError, OSError) as exc:
                                retries -= 1
                                if retries == 0:
                                    raise PTYBufferFull(f"PTY buffer full: {exc}") from exc
                                time.sleep(0.01)
                return {"status": "ok", "bytes_written": len(data)}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        if session.process is None or session.process.stdin is None:
            return {"status": "error", "error": _STDIN_UNAVAILABLE}
        try:
            stdin = session.process.stdin
            for i in range(0, len(data), chunk_size):
                stdin.write(data[i : i + chunk_size])
                stdin.flush()
                time.sleep(0.001)
            return {"status": "ok", "bytes_written": len(data)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def submit_stdin(self, session_id: str, data: str = "") -> dict:
        """向运行中进程的 stdin 发送 data + 换行（等价于按一次 Enter）。"""
        return self.write_stdin(session_id, data + "\n")

    def close_stdin(self, session_id: str) -> dict:
        """关闭运行中进程的 stdin（发 EOF），不杀进程。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "error": "Process has already finished"}
        if session._pty is not None:
            try:
                session._pty.sendeof()
                return {"status": "ok", "message": "EOF sent"}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        if session.process is None or session.process.stdin is None:
            return {"status": "error", "error": _STDIN_UNAVAILABLE}
        try:
            session.process.stdin.close()
            return {"status": "ok", "message": "stdin closed"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def list_sessions(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """列出运行中和最近结束的后台进程（可按 task_id 过滤）。"""
        with self._lock:
            all_sessions = list(self._running.values()) + list(self._finished.values())
        result = []
        for s in all_sessions:
            if task_id and s.task_id != task_id:
                continue
            entry = {
                "session_id": s.id,
                "command": clean_output(s.command)[:200],
                "cwd": clean_output(s.cwd),
                "pid": s.pid,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(s.started_at)),
                "uptime_seconds": s.uptime_seconds(),
                "status": "exited" if s.exited else "running",
                "output_preview": s.output_tail(200),
            }
            if s.exited:
                entry["exit_code"] = s.exit_code
            result.append(entry)
        return result

    def has_active_processes(self, task_id: str) -> bool:
        """task_id 下是否还有运行中进程（环境清理据此续命）。"""
        with self._lock:
            return any(s.task_id == task_id and not s.exited for s in self._running.values())

    def kill_all(self) -> int:
        """终止全部运行中进程，返回成功终止的条数。"""
        with self._lock:
            targets = [s for s in self._running.values() if not s.exited]
        return sum(self.kill_process(s.id).get("status") in {"killed", "already_exited"} for s in targets)

    # ----- Cleanup / Pruning -----
    def _prune_if_needed(self) -> None:
        """淘汰过期的已结束 session，总数超限时再淘汰最早结束的（调用方需持 ``_lock``）。"""
        now = time.time()
        # TTL 从 finished_at 起算：从 started_at 起算会让长时间运行后才退出的进程结果立刻被淘汰。
        for sid in [sid for sid, s in self._finished.items() if (now - s.finished_at) > FINISHED_TTL_SECONDS]:
            del self._finished[sid]
        if len(self._running) + len(self._finished) >= MAX_PROCESSES and self._finished:
            del self._finished[min(self._finished, key=lambda sid: self._finished[sid].finished_at)]


process_registry = ProcessRegistry()
register_active_process_checker(process_registry.has_active_processes)
# Runner 退出时回收后台进程树（Windows 另由 Job Object 兜底）；退出后已无法再管理这些进程。
atexit.register(process_registry.kill_all)


PROCESS_SCHEMA = {
    "name": "process",
    "description": (
        "Manage background processes started with terminal(background=true). "
        "Actions: 'list' (show all), 'poll' (check status + recent output), "
        "'log' (full output with pagination), 'wait' (block until done or timeout), "
        "'kill' (terminate), 'write' (send raw stdin data without newline), "
        "'submit' (send data + Enter, for answering prompts), 'close' (close stdin/send EOF). "
        "stdin actions work only for processes on the local host."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "poll", "log", "wait", "kill", "write", "submit", "close"],
                "description": "Action to perform on background processes",
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Process session ID (from terminal background output). Required for all actions except 'list'."
                ),
            },
            "data": {
                "type": "string",
                "description": "Text to send to process stdin (for 'write' and 'submit' actions)",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to block for 'wait' action. Returns partial output on timeout.",
                "minimum": 1,
            },
            "offset": {"type": "integer", "description": "Line offset for 'log' action (default: last 200 lines)"},
            "limit": {"type": "integer", "description": "Max lines to return for 'log' action", "minimum": 1},
        },
        "required": ["action"],
    },
}


def _coerce_int(value: Any, field_name: str) -> int | None:
    """模型可能把整数参数发成字符串；无法转换时抛 ValueError，由调用方转成错误信封。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer, got {type(value).__name__}: {value!r}") from None


def _handle_process(args: dict[str, Any], **kw: Any) -> str:
    action = args.get("action", "")
    # 部分模型把 session_id 发成整数。
    session_id = str(args["session_id"]) if args.get("session_id") is not None else ""
    try:
        offset = _coerce_int(args.get("offset"), "offset")
        limit = _coerce_int(args.get("limit"), "limit")
        timeout = _coerce_int(args.get("timeout"), "timeout")
    except ValueError as e:
        return tool_error(str(e))
    match action:
        case "list":
            return json.dumps(
                {"processes": process_registry.list_sessions(task_id=kw.get("task_id"))},
                ensure_ascii=False,
            )
        case "poll" | "log" | "wait" | "kill" | "write" | "submit" | "close" if not session_id:
            return tool_error(f"session_id is required for {action}")
        case "poll":
            return json.dumps(process_registry.poll(session_id), ensure_ascii=False)
        case "log":
            return json.dumps(
                process_registry.read_log(
                    session_id,
                    offset=0 if offset is None else offset,
                    limit=200 if limit is None else limit,
                ),
                ensure_ascii=False,
            )
        case "wait":
            return json.dumps(process_registry.wait(session_id, timeout=timeout), ensure_ascii=False)
        case "kill":
            return json.dumps(process_registry.kill_process(session_id), ensure_ascii=False)
        case "write":
            return json.dumps(process_registry.write_stdin(session_id, str(args.get("data", ""))), ensure_ascii=False)
        case "submit":
            return json.dumps(process_registry.submit_stdin(session_id, str(args.get("data", ""))), ensure_ascii=False)
        case "close":
            return json.dumps(process_registry.close_stdin(session_id), ensure_ascii=False)
        case _:
            return tool_error(
                f"Unknown process action: {action}. Use: list, poll, log, wait, kill, write, submit, close",
            )


registry.register_tool("process", schema=PROCESS_SCHEMA)(_handle_process)
