import contextlib
import logging
import os
import posixpath
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from utils import CREATE_NO_WINDOW, IS_WINDOWS

from ._env_base import BaseEnvironment, _popen_bash
from ._env_file_sync import (
    FileSyncManager,
    _sha256_file,
    iter_sync_files,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)

logger = logging.getLogger(__name__)

# 密码经环境传给 askpass，不落盘。
_ASKPASS_SECRET_ENV = "SPIRITAGENT_SSH_ASKPASS_SECRET"


def _ensure_ssh_available() -> None:
    if not shutil.which("ssh"):
        raise RuntimeError("SSH is not installed or not in PATH. Install OpenSSH client.")


class SSHEnvironment(BaseEnvironment):
    """基于 SSH ControlMaster 的远端终端：每个实例独占一条主连接，文件经 tar 增量同步。"""

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
        self._password = password
        self._closed = False
        control_dir = Path(tempfile.gettempdir()) / "spiritagent-ssh"
        control_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # 控制套接字按实例区分，防误切新连接。
        self.control_socket = control_dir / f"{self._session_id}.sock"
        self._askpass: Path | None = None
        try:
            _ensure_ssh_available()
            self._askpass = self._create_askpass(control_dir)
            self._establish_connection()
            self._remote_home = self._detect_remote_home()
            self._ensure_remote_dirs()
            self._sync_manager = FileSyncManager(
                get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.spiritagent"),
                bulk_upload_fn=self._ssh_bulk_upload,
                bulk_download_fn=self._ssh_bulk_download,
                delete_fn=self._ssh_delete,
            )
            self._sync_manager.sync(force=True)
            self.init_session()
        except BaseException:
            self._closed = True
            self._close_connection()
            raise

    def _create_askpass(self, control_dir: Path) -> Path | None:
        """密码模式生成 askpass 脚本（不含密码）；密钥认证（优先）或无密码时返回 None。"""
        if self.key_path or not self._password:
            return None
        if IS_WINDOWS:
            path = control_dir / f"askpass-{self._session_id}.bat"
            # 延迟扩展在解析后替换，密码特殊字符原样。
            path.write_text(
                f"@echo off\r\nsetlocal EnableDelayedExpansion\r\necho(!{_ASKPASS_SECRET_ENV}!\r\n",
                encoding="utf-8",
            )
        else:
            path = control_dir / f"askpass-{self._session_id}.sh"
            path.write_text(f"#!/bin/sh\nprintf '%s\\n' \"${_ASKPASS_SECRET_ENV}\"\n", encoding="utf-8")
            path.chmod(0o700)
        return path

    def _ssh_env(self) -> dict[str, str]:
        """子进程环境：密码模式注入 askpass（SSH_ASKPASS_REQUIRE=force 使无 TTY 也走 askpass，OpenSSH >= 8.4）。"""
        env = dict(os.environ)
        if self._askpass is not None:
            env |= {
                "SSH_ASKPASS": str(self._askpass),
                "SSH_ASKPASS_REQUIRE": "force",
                "DISPLAY": "localhost:0",
                _ASKPASS_SECRET_ENV: self._password,
            }
        return env

    def _build_ssh_command(self, extra_args: list[str] | None = None) -> list[str]:
        cmd = [
            "ssh",
            "-o",
            f"ControlPath={self.control_socket}",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPersist=300",
        ]
        # 密码模式须关 BatchMode 才能走 askpass。
        if self._askpass is None:
            cmd.extend(["-o", "BatchMode=yes"])
        # LogLevel=ERROR：告警不混进命令输出。
        cmd.extend(["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"])
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])
        if self.key_path:
            cmd.extend(["-i", self.key_path])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(f"{self.user}@{self.host}")
        return cmd

    def _run_ssh(self, remote_command: str, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self._build_ssh_command(), remote_command],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=self._ssh_env(),
            creationflags=CREATE_NO_WINDOW,
        )

    def _establish_connection(self) -> None:
        try:
            res = self._run_ssh("echo 'SSH connection established'", timeout=15)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"SSH connection to {self.user}@{self.host} timed out") from None
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or res.stdout.strip())

    def _detect_remote_home(self) -> str:
        res = self._run_ssh("echo $HOME", timeout=10)
        if res.returncode != 0 or not (home := res.stdout.strip()):
            raise RuntimeError(f"Could not determine remote $HOME: {res.stderr.strip() or 'empty output'}")
        return home

    def _ensure_remote_dirs(self) -> None:
        base = f"{self._remote_home}/.spiritagent"
        res = self._run_ssh(
            quoted_mkdir_command([base, f"{base}/skills", f"{base}/credentials", f"{base}/cache"]),
            timeout=10,
        )
        if res.returncode != 0:
            logger.warning("SSH: creating remote sync directories failed: %s", res.stderr.strip())

    def _ssh_bulk_upload(self, files: list[tuple[str, str]]) -> dict[str, str]:
        if not files:
            return {}
        base = f"{self._remote_home}/.spiritagent"
        if (parents := unique_parent_dirs(files)) and self._run_ssh(
            quoted_mkdir_command(parents),
            timeout=30,
        ).returncode != 0:
            raise RuntimeError("remote mkdir failed")
        with tempfile.TemporaryDirectory(prefix="spiritagent-ssh-bulk-") as staging:
            uploaded_hashes: dict[str, str] = {}
            for host_path, remote_path in files:
                # 远端路径用 posixpath，防反斜杠。
                rel_remote = posixpath.relpath(remote_path, base)
                if rel_remote == "." or rel_remote == ".." or rel_remote.startswith("../"):
                    raise RuntimeError(f"remote path {remote_path!r} escapes sync base {base!r}")
                staged = os.path.join(staging, rel_remote)
                os.makedirs(os.path.dirname(staged), exist_ok=True)
                shutil.copy2(host_path, staged)
                uploaded_hashes[remote_path] = _sha256_file(staged)
            tar_cmd = ["tar", "-cf", "-", "-C", staging, "."]
            ssh_cmd = self._build_ssh_command()
            ssh_cmd.append(f"tar xf - --no-overwrite-dir -C {shlex.quote(base)}")
            tar_proc = subprocess.Popen(
                tar_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
            try:
                ssh_proc = subprocess.Popen(
                    ssh_cmd,
                    stdin=tar_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._ssh_env(),
                    creationflags=CREATE_NO_WINDOW,
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
                raise RuntimeError("SSH bulk upload timed out") from None
            if tar_proc.returncode != 0:
                raise RuntimeError(
                    f"tar create failed (rc={tar_proc.returncode}): {tar_stderr_raw.decode(errors='replace').strip()}",
                )
            if ssh_proc.returncode != 0:
                raise RuntimeError(
                    f"tar extract over SSH failed (rc={ssh_proc.returncode}): {ssh_stderr.decode(errors='replace').strip()}",
                )
            return uploaded_hashes

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
                    creationflags=CREATE_NO_WINDOW,
                ).returncode
                != 0
            ):
                raise RuntimeError("SSH bulk download failed")

    def _ssh_delete(self, remote_paths: list[str]) -> None:
        if self._run_ssh(quoted_rm_command(remote_paths), timeout=10).returncode != 0:
            raise RuntimeError("remote rm failed")

    def _before_execute(self) -> None:
        self._sync_manager.sync()

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        stdin_data: str | None = None,
    ) -> subprocess.Popen:
        cmd = self._build_ssh_command()
        cmd.extend(["bash", "-l", "-c", shlex.quote(cmd_string)] if login else ["bash", "-c", shlex.quote(cmd_string)])
        return _popen_bash(cmd, stdin_data, self._ssh_env())

    def _close_connection(self) -> None:
        """关闭主连接并删除控制套接字与 askpass 脚本。"""
        if self.control_socket.exists():
            try:
                subprocess.run(
                    ["ssh", "-o", f"ControlPath={self.control_socket}", "-O", "exit", f"{self.user}@{self.host}"],
                    capture_output=True,
                    timeout=5,
                    stdin=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.warning("SSH: closing control master failed: %s", e)
            with contextlib.suppress(OSError):
                self.control_socket.unlink(missing_ok=True)
        if self._askpass is not None:
            with contextlib.suppress(OSError):
                self._askpass.unlink(missing_ok=True)
            self._askpass = None

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        logger.info("SSH: syncing files from remote...")
        try:
            self._sync_manager.sync_back()
        except Exception as e:
            logger.warning("SSH: sync_back failed: %s", e)
        self._close_connection()
