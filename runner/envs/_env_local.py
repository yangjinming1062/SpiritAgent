import contextlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from utils import (
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    append_sane_path_entries,
    find_bash,
    get_spiritagent_home,
    get_subprocess_home,
    inject_context_spiritagent_home,
    load_config,
    msys_to_windows_path,
    resolve_safe_cwd,
    terminate_tree,
)

from ._env_base import BaseEnvironment, _pipe_stdin

logger = logging.getLogger(__name__)


def _path_env_key(run_env: dict) -> str | None:
    return next((k for k in run_env if k.upper() == "PATH"), None) if IS_WINDOWS else "PATH"


def _make_run_env(env: dict) -> dict:
    """组装子进程环境：以 runner 自身环境为基底（调用方可控），叠加 `env` 覆盖。"""
    run_env = {k: str(v) if v is not None else "" for k, v in (os.environ | env).items()}
    if path_key := _path_env_key(run_env):
        run_env[path_key] = append_sane_path_entries(run_env.get(path_key, ""))
    inject_context_spiritagent_home(run_env)
    if ph := get_subprocess_home():
        run_env["HOME"] = ph
    return run_env


def _read_terminal_shell_init_config() -> tuple[list[str], bool]:
    try:
        cfg = load_config() or {}
        terminal_cfg = cfg.get("terminal") or {}
        return [str(f) for f in (terminal_cfg.get("shell_init_files") or []) if f], bool(
            terminal_cfg.get("auto_source_bashrc", True),
        )
    except Exception:
        return [], True


def _resolve_shell_init_files() -> list[str]:
    explicit, auto_bashrc = _read_terminal_shell_init_config()
    return [
        p
        for raw in (
            explicit
            if explicit
            else (["~/.profile", "~/.bash_profile", "~/.bashrc"] if auto_bashrc and not IS_WINDOWS else [])
        )
        if os.path.isfile(p := os.path.expandvars(os.path.expanduser(raw)))
    ]


def _prepend_shell_init(cmd_string: str, files: list[str]) -> str:
    if not files:
        return cmd_string
    lines = ["set +e"]
    for f in files:
        escaped = f.replace("'", "'\\''")
        lines.append(f"[ -r '{escaped}' ] && . '{escaped}' 2>/dev/null || true")
    return "\n".join(lines) + "\n" + cmd_string


class LocalEnvironment(BaseEnvironment):
    """在宿主机 shell 中直接执行命令的本地环境：复用宿主的 PATH/HOME，仅解析 cwd 与登录初始化文件。"""

    def __init__(self, cwd: str = "", timeout: int = 60, env: dict | None = None, persistent: bool = False) -> None:
        super().__init__(cwd=os.path.expanduser(cwd) if cwd else os.getcwd(), timeout=timeout, env=env)
        self._persistent = persistent
        self.init_session()

    def get_temp_dir(self) -> str:
        if IS_WINDOWS:
            try:
                cache_dir = get_spiritagent_home() / "cache" / "terminal"
            except Exception:
                cache_dir = Path(tempfile.gettempdir()) / "spiritagent_terminal"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return str(cache_dir).replace("\\", "/")
        for env_var in ("TMPDIR", "TMP", "TEMP"):
            if (candidate := self.env.get(env_var) or os.environ.get(env_var)) and candidate.startswith("/"):
                return candidate.rstrip("/") or "/"
        return (
            "/tmp"
            if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK | os.X_OK)
            else (c if (c := tempfile.gettempdir()).startswith("/") else "/tmp")
        )

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> subprocess.Popen:
        if login and (init_files := _resolve_shell_init_files()):
            cmd_string = _prepend_shell_init(cmd_string, init_files)
        args = [find_bash(), "-l", "-c", cmd_string] if login else [find_bash(), "-c", cmd_string]
        run_env = {str(k): str(v) for k, v in _make_run_env(self.env).items()}
        safe_cwd = resolve_safe_cwd(self.cwd)
        if safe_cwd != self.cwd:
            if safe_cwd != (msys_to_windows_path(self.cwd) if IS_WINDOWS else self.cwd):
                logger.warning("LocalEnvironment cwd %r is missing on disk; falling back to %r.", self.cwd, safe_cwd)
            self.cwd = safe_cwd
        proc = subprocess.Popen(
            args,
            text=True,
            env=run_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            preexec_fn=None if IS_WINDOWS else os.setsid,
            cwd=self.cwd,
            **({"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}),
        )
        if not IS_WINDOWS:
            with contextlib.suppress(ProcessLookupError):
                proc._spiritagent_pgid = os.getpgid(proc.pid)
        if stdin_data is not None:
            _pipe_stdin(proc, stdin_data)
        return proc

    def _kill_process(self, proc: subprocess.Popen) -> None:
        # 委派 helper: POSIX 走 killpg(SIGTERM) → wait → killpg(SIGKILL) → psutil 兜底,
        # Windows 走 taskkill /T → wait → taskkill /T /F。
        # ``escalate=True`` 与原内联 SIGKILL 升级语义对齐 —— 进程忽略 SIGTERM 时必须升级,
        # 否则丢失行为。``_spiritagent_pgid`` shim 仍作为 pgid 二级回退源。
        try:
            terminate_tree(
                proc,
                graceful_timeout=1.0,
                force_timeout=2.0,
                escalate=True,
                pgid=getattr(proc, "_spiritagent_pgid", None),
            )
        except Exception:
            with contextlib.suppress(Exception):
                proc.kill()

    def _update_cwd(self, result: dict) -> None:
        try:
            with open(self._cwd_file, encoding="utf-8") as f:
                cwd_path = f.read().strip()
            if IS_WINDOWS:
                cwd_path = msys_to_windows_path(cwd_path)
            if cwd_path and os.path.isdir(cwd_path):
                self.cwd = cwd_path
        except Exception:
            pass
        self._extract_cwd_from_output(result)

    def _extract_cwd_from_output(self, result: dict) -> None:
        prev_cwd = self.cwd
        super()._extract_cwd_from_output(result)
        if self.cwd != prev_cwd:
            normalized = msys_to_windows_path(self.cwd) if IS_WINDOWS else self.cwd
            if normalized and os.path.isdir(normalized):
                self.cwd = normalized
            else:
                self.cwd = prev_cwd

    def cleanup(self) -> None:
        for f in (self._snapshot_path, self._cwd_file):
            with contextlib.suppress(OSError):
                os.unlink(f)
