import contextlib
import os
import stat
import tempfile

from .constants import IS_WINDOWS


def _new_file_mode() -> int:
    # os.umask 只能读写成对调用且作用于整个进程，因此只在导入时（尚无工作线程）读取一次。
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


_NEW_FILE_MODE = _new_file_mode()


def atomic_replace(file_path: str, content: str | bytes) -> None:
    """把 ``content`` 原样（文本不转换换行）原子写入 ``file_path``（tempfile + fsync + os.replace）。

    POSIX 上覆盖已有文件保留其权限位，新建文件按 umask 取默认权限（mkstemp 本身是 0600）。Windows 的 chmod
    只控制只读位，替换只读目标本就会失败，因此不复制，避免失败后留下无法删除的只读临时文件。
    """
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
