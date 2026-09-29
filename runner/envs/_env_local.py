import contextlib
import logging
import os
import subprocess
import tempfile

from utils import (
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    append_sane_path_entries,
    cfg_get,
    find_bash,
    get_spiritagent_home,
    is_truthy_value,
    load_config,
    msys_to_windows_path,
    resolve_safe_cwd,
    sanitize_subprocess_env,
    terminate_tree,
)

from ._env_base import BaseEnvironment, _pipe_stdin

logger = logging.getLogger(__name__)


def local_run_env() -> dict[str, str]:
    """本机终端子进程环境（前台与后台共用）：继承 runner 环境，补齐常用 PATH 目录并设置 HOME / SPIRITAGENT_HOME。"""
    run_env = sanitize_subprocess_env(dict(os.environ))
    path_key = next((k for k in run_env if k.upper() == "PATH"), None) if IS_WINDOWS else "PATH"
    if path_key:
        run_env[path_key] = append_sane_path_entries(run_env.get(path_key, ""))
    return run_env


def _resolve_shell_init_files() -> list[str]:
    terminal_cfg = cfg_get(load_config(), "terminal", default={})
    explicit = [str(f) for f in (cfg_get(terminal_cfg, "shell_init_files", default=None) or []) if f]
    auto_bashrc = is_truthy_value(cfg_get(terminal_cfg, "auto_source_bashrc"), default=True)
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
    """在宿主机 shell 中直接执行命令的本地环境：子进程环境见 `local_run_env`，登录时补充用户初始化文件。"""

    def __init__(self, cwd: str = "", timeout: int = 60) -> None:
        super().__init__(cwd=os.path.expanduser(cwd) if cwd else os.getcwd(), timeout=timeout)
        self.init_session()

    def get_temp_dir(self) -> str:
        if IS_WINDOWS:
            cache_dir = get_spiritagent_home() / "cache" / "terminal"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir.as_posix()
        for env_var in ("TMPDIR", "TMP", "TEMP"):
            if (candidate := os.environ.get(env_var)) and candidate.startswith("/"):
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
        stdin_data: str | None = None,
    ) -> subprocess.Popen:
        if login and (init_files := _resolve_shell_init_files()):
            cmd_string = _prepend_shell_init(cmd_string, init_files)
        args = [find_bash(), "-l", "-c", cmd_string] if login else [find_bash(), "-c", cmd_string]
        safe_cwd = resolve_safe_cwd(self.cwd)
        if safe_cwd != self.cwd:
            if safe_cwd != (msys_to_windows_path(self.cwd) if IS_WINDOWS else self.cwd):
                logger.warning("LocalEnvironment cwd %r is missing on disk; falling back to %r.", self.cwd, safe_cwd)
            self.cwd = safe_cwd
        proc = subprocess.Popen(
            args,
            text=True,
            env=local_run_env(),
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            # POSIX 下 setsid 使 pgid == pid，超时 / 取消时整组终止；Windows 忽略该参数。
            start_new_session=True,
            cwd=self.cwd,
            creationflags=CREATE_NO_WINDOW,
        )
        if stdin_data is not None:
            _pipe_stdin(proc, stdin_data)
        return proc

    def _kill_process(self, proc: subprocess.Popen) -> None:
        # POSIX killpg(SIGTERM) → 等待 → SIGKILL；Windows taskkill /T → /T /F。进程忽略 SIGTERM 时必须升级。
        try:
            terminate_tree(proc, graceful_timeout=1.0, force_timeout=2.0, escalate=True)
        except Exception as e:
            logger.warning("terminate_tree failed for pid %s: %s; killing direct child only", proc.pid, e)
            with contextlib.suppress(OSError):
                proc.kill()

    def _update_cwd(self, result: dict) -> None:
        try:
            with open(self._cwd_file, encoding="utf-8") as f:
                cwd_path = f.read().strip()
        except OSError:
            cwd_path = ""
        if IS_WINDOWS:
            cwd_path = msys_to_windows_path(cwd_path)
        if cwd_path and os.path.isdir(cwd_path):
            self.cwd = cwd_path
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
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.debug("LocalEnvironment cleanup could not remove %s: %s", f, e)
