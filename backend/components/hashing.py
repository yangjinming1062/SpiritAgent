import base64
import hashlib
from pathlib import Path


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha512_b64(path: Path) -> str:
    """文件 SHA-512 → base64（electron-updater 格式）。"""
    h = hashlib.sha512()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()
