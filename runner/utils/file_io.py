import contextlib
import os
import stat
import tempfile

from .constants import IS_WINDOWS


def _new_file_mode() -> int:
    # umask 全进程生效，导入时读一次。
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


_NEW_FILE_MODE = _new_file_mode()


def atomic_replace(file_path: str, content: str | bytes) -> None:
    """原子写入（tempfile+fsync+replace）；POSIX 保留权限位，Windows 不 chmod 临时文件。"""
    if dir_name := os.path.dirname(file_path):
        os.makedirs(dir_name, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dir_name or None)
    try:
        with (
            os.fdopen(fd, "wb")
            if isinstance(content, bytes)
            else os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp
        ):
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        if not IS_WINDOWS:
            try:
                mode = stat.S_IMODE(os.stat(file_path).st_mode)
            except FileNotFoundError:
                mode = _NEW_FILE_MODE
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, file_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
