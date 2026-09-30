import contextlib
import hashlib
import logging
import os
import posixpath
import shlex
import shutil
import signal
import sys
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from utils import get_credential_file_mounts, get_spiritagent_home, iter_cache_files, iter_skills_files

# 文件锁用 stdlib（POSIX flock / Windows msvcrt），不进 pyproject。
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

_SYNC_INTERVAL_SECONDS = 5.0

type BulkUploadFn = Callable[[list[tuple[str, str]]], None]
type BulkDownloadFn = Callable[[Path], None]
type DeleteFn = Callable[[list[str]], None]
type GetFilesFn = Callable[[], list[tuple[str, str]]]


def _file_mtime_key(host_path: str) -> tuple[float, int] | None:
    try:
        return ((st := Path(host_path).stat()).st_mtime, st.st_size)
    except OSError:
        return None


def iter_sync_files(container_base: str) -> list[tuple[str, str]]:
    """枚举需要同步到远端 `container_base` 下的 (host_path, remote_path) 列表：凭据、技能、缓存目录。"""
    mounts = (
        get_credential_file_mounts(container_base)
        + iter_skills_files(container_base)
        + iter_cache_files(container_base)
    )
    return [(m["host_path"], m["container_path"]) for m in mounts]


def quoted_rm_command(remote_paths: list[str]) -> str:
    """拼接一条 `rm -f ...` 命令串，路径自动 shlex 转义。"""
    return "rm -f " + shlex.join(remote_paths)


def quoted_mkdir_command(dirs: list[str]) -> str:
    """拼接一条 `mkdir -p ...` 命令串，路径自动 shlex 转义。"""
    return "mkdir -p " + shlex.join(dirs)


def unique_parent_dirs(files: list[tuple[str, str]]) -> list[str]:
    """从 (host, remote) 列表中提取所有不重复的 remote 父目录（POSIX 风格）。"""
    return sorted({posixpath.dirname(remote) for _, remote in files})


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


_SYNC_BACK_MAX_RETRIES = 3
_SYNC_BACK_BACKOFF = (2, 4, 8)
_SYNC_BACK_MAX_BYTES = 2 * 1024 * 1024 * 1024


class FileSyncManager:
    """双向同步管理器：上传依赖目录到远端 / 下载远端变更回本地，通过文件锁串行化避免冲突。"""

    def __init__(
        self,
        get_files_fn: GetFilesFn,
        bulk_upload_fn: BulkUploadFn,
        bulk_download_fn: BulkDownloadFn,
        delete_fn: DeleteFn,
    ) -> None:
        self._get_files_fn = get_files_fn
        self._bulk_upload_fn = bulk_upload_fn
        self._bulk_download_fn = bulk_download_fn
        self._delete_fn = delete_fn
        self._synced_files: dict[str, tuple[float, int]] = {}
        self._pushed_hashes: dict[str, str] = {}
        self._last_sync_time: float = 0.0

    def sync(self, *, force: bool = False) -> None:
        if not force and time.monotonic() - self._last_sync_time < _SYNC_INTERVAL_SECONDS:
            return
        current_files = self._get_files_fn()
        current_remote_paths = {remote for _, remote in current_files}
        to_upload = [
            (hp, rp)
            for hp, rp in current_files
            if (fk := _file_mtime_key(hp)) is not None and self._synced_files.get(rp) != fk
        ]
        to_delete = [p for p in self._synced_files if p not in current_remote_paths]
        if not to_upload and not to_delete:
            self._last_sync_time = time.monotonic()
            return
        prev_files, prev_hashes = dict(self._synced_files), dict(self._pushed_hashes)
        try:
            if to_upload:
                self._bulk_upload_fn(to_upload)
            if to_delete:
                self._delete_fn(to_delete)
            new_files = {rp: fk for hp, rp in current_files if (fk := _file_mtime_key(hp)) is not None}
            self._pushed_hashes.update({rp: _sha256_file(hp) for hp, rp in to_upload})
            for p in to_delete:
                new_files.pop(p, None)
                self._pushed_hashes.pop(p, None)
            self._synced_files = new_files
        except Exception as exc:
            self._synced_files, self._pushed_hashes = prev_files, prev_hashes
            logger.warning("file_sync: sync failed, rolled back state: %s", exc)
        self._last_sync_time = time.monotonic()

    def sync_back(self) -> None:
        if not self._pushed_hashes and not self._synced_files:
            return
        lock_path = get_spiritagent_home() / ".sync.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(_SYNC_BACK_MAX_RETRIES):
            try:
                self._sync_back_once(lock_path)
                return
            except Exception as exc:
                if attempt == _SYNC_BACK_MAX_RETRIES - 1:
                    logger.warning("sync_back: all attempts failed: %s", exc)
                else:
                    logger.warning("sync_back: attempt %d failed, retrying...", attempt + 1)
                    time.sleep(_SYNC_BACK_BACKOFF[attempt])

    def _sync_back_once(self, lock_path: Path) -> None:
        on_main = threading.current_thread() is threading.main_thread()
        deferred = []
        original = signal.getsignal(signal.SIGINT) if on_main else None
        if on_main:
            signal.signal(signal.SIGINT, lambda s, f: deferred.append((s, f)))
        try:
            self._sync_back_locked(lock_path)
        finally:
            if on_main and original is not None:
                signal.signal(signal.SIGINT, original)
                if deferred:
                    if os.name == "posix":
                        os.kill(os.getpid(), signal.SIGINT)
                    else:
                        # Windows kill(SIGINT) 实为 TerminateProcess，须直接抛中断。
                        raise KeyboardInterrupt

    def _sync_back_locked(self, lock_path: Path) -> None:
        # Windows msvcrt.locking 须已有字节；0 字节会 Errno 22。写 1 字节占位，已有占位勿再写。
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w+b") as f:
            if f.read(1) != b"\x00":
                f.write(b"\x00")
                f.flush()
            try:
                if sys.platform == "win32":
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    fcntl.flock(f, fcntl.LOCK_EX)
                self._sync_back_impl()
            finally:
                try:
                    if sys.platform == "win32":
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(f, fcntl.LOCK_UN)
                except OSError as e:
                    logger.debug("file_sync: unlock failed for %s: %s", lock_path, e)

    def _sync_back_impl(self) -> None:
        mapping = list(self._get_files_fn())
        # mkstemp 后显式关闭：Windows 下子进程写不了我们仍持有的文件。
        fd, tar_name = tempfile.mkstemp(suffix=".tar")
        os.close(fd)
        try:
            self._bulk_download_fn(Path(tar_name))
            if (tar_size := os.path.getsize(tar_name)) > _SYNC_BACK_MAX_BYTES:
                logger.warning("sync_back: remote tar %d bytes exceeds cap", tar_size)
                return
            with tempfile.TemporaryDirectory(prefix="spiritagent-sync-back-") as staging:
                with tarfile.open(tar_name) as tar:
                    tar.extractall(staging, filter="data")
                applied = 0
                for dp, _, fnames in os.walk(staging):
                    for fn in fnames:
                        staged = os.path.join(dp, fn)
                        # 远端路径用 posixpath，Windows relpath 会出反斜杠。
                        remote = "/" + os.path.relpath(staged, staging).replace(os.sep, "/")
                        if (pushed := self._pushed_hashes.get(remote)) is not None and _sha256_file(staged) == pushed:
                            continue
                        if not (
                            host := self._resolve_host_path(remote, mapping) or self._infer_host_path(remote, mapping)
                        ):
                            logger.debug("sync_back: skipping %s (no host mapping)", remote)
                            continue
                        if os.path.exists(host) and pushed is not None and _sha256_file(host) != pushed:
                            logger.warning(
                                "sync_back: conflict on %s — applying remote version (last-write-wins).",
                                remote,
                            )
                        os.makedirs(os.path.dirname(host), exist_ok=True)
                        shutil.copy2(staged, host)
                        applied += 1
                if applied:
                    logger.info("sync_back: applied %d changed file(s)", applied)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tar_name)

    def _resolve_host_path(self, remote_path: str, mapping: list[tuple[str, str]]) -> str | None:
        return next((h for h, r in mapping if r == remote_path), None)

    def _infer_host_path(self, remote_path: str, mapping: list[tuple[str, str]]) -> str | None:
        # 用 posixpath.dirname 保留 POSIX 前缀。
        return next(
            (
                str(Path(host).parent) + remote_path[len(r_dir) :]
                for host, remote in mapping
                if remote_path.startswith((r_dir := posixpath.dirname(remote)) + "/")
            ),
            None,
        )
