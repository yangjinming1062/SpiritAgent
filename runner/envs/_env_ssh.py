import contextlib
import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from utils import CREATE_NO_WINDOW, IS_WINDOWS

from ._env_base import BaseEnvironment, _popen_bash
from ._env_file_sync import (
    FileSyncManager,
    iter_sync_files,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)

# Windows：抑制 runner 每次派生 ssh 子进程时闪现的控制台窗口。
_NO_WINDOW = {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}

logger = logging.getLogger(__name__)


def _ensure_ssh_available() -> None:
    if not shutil.which("ssh") or not shutil.which("scp"):
        raise RuntimeError("SSH or SCP is not installed or not in PATH. Install OpenSSH client.")


class SSHEnvironment(BaseEnvironment):
    """基于 SSH ControlMaster 的远端终端：复用持久连接减少认证开销，文件通过 SCP 增量同步。"""

    def __init__(
        self,
        host: str,
        user: str,
        cwd: str = "~",
        timeout: int = 60,
        port: int = 22,
        key_path: str = "",
        password: str = "",
    ) -> None:
        super().__init__(cwd=cwd, timeout=timeout)
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.password = password
        self.control_dir = Path(tempfile.gettempdir()) / "spiritagent-ssh"
        self.control_dir.mkdir(parents=True, exist_ok=True)
        _socket_id = hashlib.sha256(f"{user}@{host}:{port}".encode()).hexdigest()[:16]
        self.control_socket = self.control_dir / f"{_socket_id}.sock"
        # 默认占位 None; ``_create_askpass`` 在连通性验证后才会写入明文密码到磁盘,
        # 且若后续任意一步抛错, ``finally`` 会立即把临时脚本删掉, 不让 askpass 残留。
        self._askpass: Path | None = None
        try:
            _ensure_ssh_available()
            self._askpass = self._create_askpass()
            self._establish_connection()
            self._remote_home = self._detect_remote_home()
            self._ensure_remote_dirs()
            self._sync_manager = FileSyncManager(
                get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.spiritagent"),
                upload_fn=self._scp_upload,
                delete_fn=self._ssh_delete,
                bulk_upload_fn=self._ssh_bulk_upload,
                bulk_download_fn=self._ssh_bulk_download,
            )
            self._sync_manager.sync(force=True)
            self.init_session()
        except Exception:
            self._cleanup_askpass_on_init_failure()
            raise

    def _cleanup_askpass_on_init_failure(self) -> None:
        """``__init__`` 失败时回收 askpass 脚本与 control socket, 防止明文密码残留在 ``tempfile.gettempdir()``。"""
        if self._askpass is not None:
            with contextlib.suppress(OSError):
                self._askpass.unlink(missing_ok=True)
            self._askpass = None
        if getattr(self, "control_socket", None) is not None:
            with contextlib.suppress(OSError):
                self.control_socket.unlink(missing_ok=True)

    def _create_askpass(self) -> Path | None:
        """密码模式生成一次性 askpass 脚本；密钥认证（优先）或无密码时返回 None。"""
        if self.key_path or not self.password:
            return None
        path = self.control_dir / ("askpass.bat" if IS_WINDOWS else "askpass.sh")
        if IS_WINDOWS:
            escaped = self.password.translate(
                str.maketrans({"%": "%%", "^": "^^", "&": "^&", "|": "^|", "<": "^<", ">": "^>", "(": "^(", ")": "^)"}),
            )
            path.write_text(f"@echo off\r\necho {escaped}\r\n", encoding="utf-8")
        else:
            escaped = self.password.replace("'", "'\\''")
            path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{escaped}'\n", encoding="utf-8")
            path.chmod(0o700)
        return path

    def _ssh_env(self) -> dict[str, str]:
        """子进程环境：密码模式注入 SSH_ASKPASS（force 使无 TTY 也走 askpass，OpenSSH >= 8.4）。"""
        if self._askpass is None:
            return dict(os.environ)
        return {
            **os.environ,
            "SSH_ASKPASS": str(self._askpass),
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": "localhost:0",
        }

    def _build_ssh_command(self, extra_args: list | None = None) -> list:
        cmd = [
            "ssh",
            "-o",
            f"ControlPath={self.control_socket}",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPersist=300",
        ]
        # BatchMode 禁掉一切交互提示——密码模式必须放开才能触发 askpass。
        if self._askpass is None:
            cmd.extend(["-o", "BatchMode=yes"])
        cmd.extend(["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"])
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])
        if self.key_path:
            cmd.extend(["-i", self.key_path])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(f"{self.user}@{self.host}")
        return cmd

    def _establish_connection(self) -> None:
        cmd = self._build_ssh_command()
        cmd.append("echo 'SSH connection established'")
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                stdin=subprocess.DEVNULL,
                env=self._ssh_env(),
                **_NO_WINDOW,
            )
            if res.returncode != 0:
                raise RuntimeError(res.stderr.strip() or res.stdout.strip())
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"SSH connection to {self.user}@{self.host} timed out")

    def _detect_remote_home(self) -> str:
        try:
            cmd = self._build_ssh_command()
            cmd.append("echo $HOME")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
                env=self._ssh_env(),
                **_NO_WINDOW,
            )
            if (home := result.stdout.strip()) and result.returncode == 0:
                return home
        except Exception:
            pass
        return "/root" if self.user == "root" else f"/home/{self.user}"

    def _ensure_remote_dirs(self) -> None:
        base = f"{self._remote_home}/.spiritagent"
        cmd = self._build_ssh_command()
        cmd.append(quoted_mkdir_command([base, f"{base}/skills", f"{base}/credentials", f"{base}/cache"]))
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            env=self._ssh_env(),
            **_NO_WINDOW,
        )

    def _scp_upload(self, host_path: str, remote_path: str) -> None:
        mkdir_cmd = self._build_ssh_command()
        mkdir_cmd.append(f"mkdir -p {shlex.quote(str(Path(remote_path).parent))}")
        subprocess.run(
            mkdir_cmd,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            env=self._ssh_env(),
            **_NO_WINDOW,
        )
        scp_cmd = ["scp", "-o", f"ControlPath={self.control_socket}"]
        if self.port != 22:
            scp_cmd.extend(["-P", str(self.port)])
        if self.key_path:
            scp_cmd.extend(["-i", self.key_path])
        scp_cmd.extend([host_path, f"{self.user}@{self.host}:{remote_path}"])
        if (
            subprocess.run(
                scp_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
                env=self._ssh_env(),
                **_NO_WINDOW,
            ).returncode
            != 0
        ):
            raise RuntimeError(f"scp failed for {remote_path}")

    def _ssh_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        if not files:
            return
        base = f"{self._remote_home}/.spiritagent"
        if parents := unique_parent_dirs(files):
            cmd = self._build_ssh_command()
            cmd.append(quoted_mkdir_command(parents))
            if (
                subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    stdin=subprocess.DEVNULL,
                    env=self._ssh_env(),
                    **_NO_WINDOW,
                ).returncode
                != 0
            ):
                raise RuntimeError("remote mkdir failed")
        with tempfile.TemporaryDirectory(prefix="spiritagent-ssh-bulk-") as staging:
            for host_path, remote_path in files:
                try:
                    rel_remote = os.path.relpath(remote_path, base)
                except ValueError as exc:
                    raise RuntimeError(f"remote path {remote_path!r} is not under sync base {base!r}") from exc
                if rel_remote == "." or rel_remote.startswith("../"):
                    raise RuntimeError(f"remote path {remote_path!r} escapes sync base {base!r}")
                staged = os.path.join(staging, rel_remote)
                os.makedirs(os.path.dirname(staged), exist_ok=True)
                try:
                    os.symlink(os.path.abspath(host_path), staged)
                except OSError as e:
                    if getattr(e, "winerror", None) == 1314:
                        shutil.copy2(host_path, staged)
                    else:
                        raise
            tar_cmd = ["tar", "-chf", "-", "-C", staging, "."]
            ssh_cmd = self._build_ssh_command()
            ssh_cmd.append(f"tar xf - --no-overwrite-dir -C {shlex.quote(base)}")
            tar_proc = subprocess.Popen(
                tar_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **_NO_WINDOW,
            )
            try:
                ssh_proc = subprocess.Popen(
                    ssh_cmd,
                    stdin=tar_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._ssh_env(),
                    **_NO_WINDOW,
                )
            except Exception:
                tar_proc.kill()
                tar_proc.wait()
                raise
            tar_proc.stdout.close()
            try:
                _, ssh_stderr = ssh_proc.communicate(timeout=120)
                tar_stderr_raw = (
                    tar_proc.communicate(timeout=10)[1]
                    if tar_proc.poll() is None
                    else (tar_proc.stderr.read() if tar_proc.stderr else b"")
                )
            except subprocess.TimeoutExpired:
                for p in (tar_proc, ssh_proc):
                    p.kill()
                    p.wait()
                raise RuntimeError("SSH bulk upload timed out")
            if tar_proc.returncode != 0:
                raise RuntimeError(
                    f"tar create failed (rc={tar_proc.returncode}): {tar_stderr_raw.decode(errors='replace').strip()}",
                )
            if ssh_proc.returncode != 0:
                raise RuntimeError(
                    f"tar extract over SSH failed (rc={ssh_proc.returncode}): {ssh_stderr.decode(errors='replace').strip()}",
                )

    def _ssh_bulk_download(self, dest: Path) -> None:
        ssh_cmd = self._build_ssh_command()
        ssh_cmd.append(f"tar cf - -C / {shlex.quote(f'{self._remote_home}/.spiritagent'.lstrip('/'))}")
        with open(dest, "wb") as f:
            if (
                subprocess.run(
                    ssh_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=f,
                    stderr=subprocess.PIPE,
                    timeout=120,
                    env=self._ssh_env(),
                    **_NO_WINDOW,
                ).returncode
                != 0
            ):
                raise RuntimeError("SSH bulk download failed")

    def _ssh_delete(self, remote_paths: list[str]) -> None:
        cmd = self._build_ssh_command()
        cmd.append(quoted_rm_command(remote_paths))
        if (
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
                env=self._ssh_env(),
                **_NO_WINDOW,
            ).returncode
            != 0
        ):
            raise RuntimeError("remote rm failed")

    def _before_execute(self) -> None:
        self._sync_manager.sync()

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> subprocess.Popen:
        cmd = self._build_ssh_command()
        cmd.extend(["bash", "-l", "-c", shlex.quote(cmd_string)] if login else ["bash", "-c", shlex.quote(cmd_string)])
        return _popen_bash(cmd, stdin_data, env=self._ssh_env())

    def cleanup(self) -> None:
        # 同步清理须容错: 部分 ``__init__`` 失败的实例没有 ``_sync_manager`` 属性。
        sync_mgr = getattr(self, "_sync_manager", None)
        if sync_mgr is not None:
            logger.info("SSH: syncing files from sandbox...")
            try:
                sync_mgr.sync_back()
            except Exception as e:
                logger.warning("SSH: sync_back failed: %s", e)
        askpass = getattr(self, "_askpass", None)
        if askpass is not None:
            with contextlib.suppress(OSError):
                Path(askpass).unlink(missing_ok=True)
        control_socket = getattr(self, "control_socket", None)
        if control_socket is not None and Path(control_socket).exists():
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["ssh", "-o", f"ControlPath={control_socket}", "-O", "exit", f"{self.user}@{self.host}"],
                    capture_output=True,
                    timeout=5,
                    stdin=subprocess.DEVNULL,
                    **_NO_WINDOW,
                )
            with contextlib.suppress(OSError):
                Path(control_socket).unlink(missing_ok=True)
