import codecs
import contextlib
import logging
import os
import select
import shlex
import subprocess
import threading
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, Protocol

from utils import CREATE_NO_WINDOW, cfg_get, get_spiritagent_home, is_interrupted, load_config

from ._cmd_rewrite import _rewrite_compound_background, _transform_sudo_command

logger = logging.getLogger(__name__)

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.PeekNamedPipe.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.PeekNamedPipe.restype = wintypes.BOOL


def _file_mtime_key(host_path: str) -> tuple[float, int] | None:
    try:
        return ((st := Path(host_path).stat()).st_mtime, st.st_size)
    except OSError:
        return None


def get_sandbox_dir() -> Path:
    """解析终端沙箱根目录：配置覆盖优先；否则以应用根目录下的 `sandboxes` 作为基目录。"""
    override = cfg_get(load_config(), "terminal", "sandbox_dir")
    base = Path(str(override)) if override else (get_spiritagent_home() / "sandboxes")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _pipe_stdin(proc: subprocess.Popen, data: str) -> None:
    def _write() -> None:
        try:
            (target := getattr(proc.stdin, "buffer", proc.stdin)).write(
                data.encode("utf-8") if isinstance(data, str) else data,
            )
            target.close()
        except (BrokenPipeError, OSError):
            pass

    threading.Thread(target=_write, daemon=True).start()


def _popen_bash(cmd: list[str], stdin_data: str | None = None, **kwargs) -> subprocess.Popen:
    # Windows：抑制每次 bash 子进程闪现的控制台窗口。
    if os.name == "nt":
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        text=True,
        **kwargs,
    )
    if stdin_data is not None:
        _pipe_stdin(proc, stdin_data)
    return proc


class ProcessHandle(Protocol):
    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    @property
    def stdout(self) -> IO[str] | None: ...
    @property
    def returncode(self) -> int | None: ...


def _cwd_marker(session_id: str) -> str:
    return f"SPIRITAGENT_CWD_{session_id}__"


class BaseEnvironment(ABC):
    _stdin_mode: str = "pipe"
    _snapshot_timeout: int = 30

    def get_temp_dir(self) -> str:
        return "/tmp"

    def __init__(self, cwd: str, timeout: int, env: dict | None = None) -> None:
        self.cwd = cwd
        self.timeout = timeout
        self.env = env or {}
        self._session_id = uuid.uuid4().hex[:12]
        temp_dir = self.get_temp_dir().rstrip("/") or "/"
        self._snapshot_path = f"{temp_dir}/spiritagent-snap-{self._session_id}.sh"
        self._cwd_file = f"{temp_dir}/spiritagent-cwd-{self._session_id}.txt"
        self._cwd_marker = _cwd_marker(self._session_id)
        self._snapshot_ready = False
        self._snapshot_created_at: float = 0.0

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> ProcessHandle:
        raise NotImplementedError(f"{type(self).__name__} must implement _run_bash()")

    @abstractmethod
    def cleanup(self) -> None: ...
    def init_session(self) -> None:
        bootstrap = (
            f"export -p > {shlex.quote(self._snapshot_path)}\n"
            f"declare -f | grep -vE '^_[^_]' >> {shlex.quote(self._snapshot_path)}\n"
            f"alias -p >> {shlex.quote(self._snapshot_path)}\n"
            f"echo 'shopt -s expand_aliases' >> {shlex.quote(self._snapshot_path)}\n"
            f"echo 'set +e' >> {shlex.quote(self._snapshot_path)}\n"
            f"echo 'set +u' >> {shlex.quote(self._snapshot_path)}\n"
            f"builtin cd {shlex.quote(self.cwd)} 2>/dev/null || true\n"
            f"pwd -P > {shlex.quote(self._cwd_file)} 2>/dev/null || true\n"
            f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\"\n"
        )
        try:
            proc = self._run_bash(bootstrap, login=True, timeout=self._snapshot_timeout)
            result = self._wait_for_process(proc, timeout=self._snapshot_timeout)
            self._snapshot_ready = True
            self._snapshot_created_at = time.time()
            self._update_cwd(result)
            logger.info("Session snapshot created (session=%s, cwd=%s)", self._session_id, self.cwd)
        except Exception as exc:
            logger.warning(
                "init_session failed (session=%s): %s — falling back to bash -l per command",
                self._session_id,
                exc,
            )
            self._snapshot_ready = False
            self._snapshot_created_at = 0.0

    @staticmethod
    def _quote_cwd_for_cd(cwd: str) -> str:
        return (
            cwd
            if cwd == "~"
            else "$HOME"
            if cwd == "~/"
            else f"$HOME/{shlex.quote(cwd[2:])}"
            if cwd.startswith("~/")
            else shlex.quote(cwd)
        )

    def _wrap_command(self, command: str, cwd: str) -> str:
        escaped = command.replace("'", "'\\''")
        _quoted_snap = shlex.quote(self._snapshot_path)
        _quoted_cwd_file = shlex.quote(self._cwd_file)
        parts = []
        if self._snapshot_ready:
            parts.append(f"source {_quoted_snap} >/dev/null 2>&1 || true")
        parts.append(f"builtin cd -- {self._quote_cwd_for_cd(cwd)} || exit 126")
        parts.append(f"eval '{escaped}'")
        parts.append("__spiritagent_ec=$?")
        if self._snapshot_ready:
            parts.append(f"export -p > {_quoted_snap} 2>/dev/null || true")
        parts.append(f"pwd -P > {_quoted_cwd_file} 2>/dev/null || true")
        parts.append(f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\"")
        parts.append("exit $__spiritagent_ec")
        return "\n".join(parts)

    @staticmethod
    def _embed_stdin_heredoc(command: str, stdin_data: str) -> str:
        delimiter = f"SPIRITAGENT_STDIN_{uuid.uuid4().hex[:12]}"
        return f"{command} << '{delimiter}'\n{stdin_data}\n{delimiter}"

    def _wait_for_process(self, proc: ProcessHandle, timeout: int = 120) -> dict:
        output_chunks: list[str] = []
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def _drain_iterable(stream) -> None:
            try:
                for piece in stream:
                    if piece is not None:
                        output_chunks.append(decoder.decode(piece) if isinstance(piece, bytes) else str(piece))
            except Exception as e:
                logger.debug("process output drain stopped early: %s", e)

        def _drain() -> None:
            if (stream := proc.stdout) is None:
                return
            try:
                fileno = getattr(stream, "fileno", None)
                fd = fileno() if callable(fileno) else None
                if not isinstance(fd, int) or fd < 0:
                    _drain_iterable(stream)
                elif os.name == "nt":
                    # 当有孤儿后代仍持有管道的写端时，裸阻塞 os.read 永远不会返回；用 PeekNamedPipe 轮询，在直接子进程退出且管道持续空时退出（与 POSIX 的 select 分支对称）。
                    handle = msvcrt.get_osfhandle(fd)
                    avail = wintypes.DWORD(0)
                    idle_after_exit = 0
                    while True:
                        peeked = _kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None)
                        if peeked and avail.value > 0:
                            if not (chunk := os.read(fd, min(4096, avail.value))):
                                break
                            output_chunks.append(decoder.decode(chunk))
                            idle_after_exit = 0
                        elif not peeked:
                            # 写端关闭（broken pipe）：先排空剩余缓冲数据，再让 read 返回 b"" 退出。
                            if not (chunk := os.read(fd, 4096)):
                                break
                            output_chunks.append(decoder.decode(chunk))
                        elif proc.poll() is not None:
                            if (idle_after_exit := idle_after_exit + 1) >= 3:
                                break
                        else:
                            time.sleep(0.05)
                else:
                    idle_after_exit = 0
                    while True:
                        ready, _, _ = select.select([fd], [], [], 0.1)
                        if ready:
                            if not (chunk := os.read(fd, 4096)):
                                break
                            output_chunks.append(decoder.decode(chunk))
                            idle_after_exit = 0
                        elif proc.poll() is not None:
                            if (idle_after_exit := idle_after_exit + 1) >= 3:
                                break
            except (ValueError, OSError):
                pass
            finally:
                try:
                    if tail := decoder.decode(b"", final=True):
                        output_chunks.append(tail)
                except Exception:
                    pass

        drain_thread = threading.Thread(target=_drain, daemon=True, name="proc-output-drain")
        drain_thread.start()
        deadline = time.monotonic() + timeout
        try:
            _poll_sleep = 0.005
            while proc.poll() is None:
                if is_interrupted():
                    self._kill_process(proc)
                    drain_thread.join(timeout=2)
                    return {"output": "".join(output_chunks) + "\n[Command interrupted]", "returncode": 130}
                if time.monotonic() > deadline:
                    self._kill_process(proc)
                    drain_thread.join(timeout=2)
                    partial = "".join(output_chunks)
                    return {
                        "output": f"{partial}\n[Command timed out after {timeout}s]"
                        if partial
                        else f"[Command timed out after {timeout}s]",
                        "returncode": 124,
                    }
                time.sleep(_poll_sleep)
                _poll_sleep = min(_poll_sleep * 1.5, 0.2)
        except (KeyboardInterrupt, SystemExit):
            try:
                self._kill_process(proc)
                drain_thread.join(timeout=2)
            except Exception:
                pass
            raise
        drain_thread.join(timeout=2)
        with contextlib.suppress(Exception):
            proc.stdout.close()
        return {"output": "".join(output_chunks), "returncode": proc.returncode}

    def _kill_process(self, proc: ProcessHandle) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            proc.kill()

    def _update_cwd(self, result: dict) -> None:
        self._extract_cwd_from_output(result)

    def _extract_cwd_from_output(self, result: dict) -> None:
        output = result.get("output", "")
        marker = self._cwd_marker
        if (
            (last := output.rfind(marker)) != -1
            and (first := output.rfind(marker, max(0, last - 4096), last)) != -1
            and first != last
        ):
            if cwd_path := output[first + len(marker) : last].strip():
                self.cwd = cwd_path
            line_start = first if (ls := output.rfind("\n", 0, first)) == -1 else ls
            line_end = len(output) if (le := output.find("\n", last + len(marker))) == -1 else le + 1
            result["output"] = output[:line_start] + output[line_end:]

    def _before_execute(self) -> None:
        if self._snapshot_ready:
            try:
                max_age = int(cfg_get(load_config(), "terminal", "env_snapshot_max_age", default=86400))
            except (ValueError, TypeError):
                max_age = 86400
            if not os.path.exists(self._snapshot_path) or (
                max_age > 0 and (time.time() - self._snapshot_created_at > max_age)
            ):
                self.init_session()

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
        rewrite_compound_background: bool = True,
    ) -> dict:
        self._before_execute()
        exec_command, sudo_stdin = self._prepare_command(command)
        if rewrite_compound_background:
            exec_command = _rewrite_compound_background(exec_command)
        effective_stdin = (
            sudo_stdin + stdin_data if sudo_stdin is not None and stdin_data is not None else (sudo_stdin or stdin_data)
        )
        if effective_stdin and self._stdin_mode == "heredoc":
            exec_command = self._embed_stdin_heredoc(exec_command, effective_stdin)
            effective_stdin = None
        to = timeout or self.timeout
        wrapped = self._wrap_command(exec_command, cwd or self.cwd)
        proc = self._run_bash(wrapped, login=not self._snapshot_ready, timeout=to, stdin_data=effective_stdin)
        result = self._wait_for_process(proc, timeout=to)
        self._update_cwd(result)
        return result

    def stop(self) -> None:
        self.cleanup()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.cleanup()

    def _prepare_command(self, command: str) -> tuple[str, str | None]:
        return _transform_sudo_command(command)
