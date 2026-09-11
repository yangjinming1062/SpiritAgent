import contextlib
import os
import tempfile


def atomic_replace(file_path: str, content: str) -> None:
    """把 ``content`` 原子地写入 ``file_path``（tempfile + fsync + os.replace）。"""
    if dir_name := os.path.dirname(file_path):
        os.makedirs(dir_name, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dir_name or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, file_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
