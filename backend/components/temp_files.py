import contextlib
import json
import math
import re
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

from .config import SETTINGS
from .logger import get_logger

logger = get_logger(__name__)

_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _storage_dir() -> Path:
    d = Path(SETTINGS.data_dir) / "temp-media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path(file_id: str) -> Path:
    return _storage_dir() / f"{file_id}.json"


def _media_path(file_id: str, ext: str) -> Path:
    return _storage_dir() / f"{file_id}.{ext}"


def _valid_file_id(file_id: str) -> bool:
    return _FILE_ID_RE.fullmatch(file_id) is not None


def _read_metadata(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _metadata_created_at(meta: dict) -> float | None:
    try:
        created_at = float(meta.get("created_at", 0))
    except (TypeError, ValueError):
        return None
    return created_at if math.isfinite(created_at) else None


def _metadata_path(meta: dict) -> Path | None:
    value = meta.get("path")
    if not isinstance(value, str) or not value:
        return None
    return Path(value)


def owned_temp_files(file_id: str, user_id: int) -> list[Path]:
    """返回该用户名下临时文件的元数据与数据文件路径（备份导出用）；ID 非法、元数据缺失或归属其他用户时为空。"""
    if not _valid_file_id(file_id):
        return []
    meta_path = _meta_path(file_id)
    meta = _read_metadata(meta_path)
    if meta is None or meta.get("user_id") != user_id or (recorded := _metadata_path(meta)) is None:
        return []
    # 元数据记录写入时的绝对路径；按文件名在当前目录定位，数据目录迁移后仍可用。
    media = meta_path.with_name(recorded.name)
    return [meta_path, media] if media.is_file() and media != meta_path else [meta_path]


def save_file(data: bytes, content_type: str, ext: str, *, user_id: int) -> tuple[str, str]:
    """保存到 temp 存储，返回 (file_id, public_url)；``user_id`` 记录归属供删除用户与备份定位；写失败时清理半成品文件避免无 TTL 孤儿。"""
    file_id = secrets.token_urlsafe(16)
    filepath = _media_path(file_id, ext)

    meta = {
        "path": str(filepath),
        "user_id": user_id,
        "created_at": time.time(),
        "content_type": content_type,
        "size": len(data),
    }
    try:
        with open(filepath, "wb") as f:
            f.write(data)
        with open(_meta_path(file_id), "w") as f:
            json.dump(meta, f)
    except OSError as exc:
        _safe_unlink(filepath)
        _safe_unlink(_meta_path(file_id))
        logger.warning("temp file write failed; partial removed", extra={"file_id": file_id, "error": str(exc)})
        raise

    public_url = _build_public_url(file_id)
    logger.info(
        "Temp file saved",
        extra={"file_id": file_id, "size": len(data), "user_id": user_id},
    )
    return file_id, public_url


def _build_public_url(file_id: str) -> str:
    return f"/api/media/files/{file_id}"


def get_file_path(file_id: str) -> tuple[Path, str] | None:
    """按 ID 取文件路径与 content_type；未找到/已过期返 None。"""
    if not _valid_file_id(file_id):
        return None
    meta = _read_metadata(_meta_path(file_id))
    if meta is None:
        return None
    created_at = _metadata_created_at(meta)
    if created_at is None or time.time() - created_at > SETTINGS.temp_file_ttl_hours * 3600:
        return None
    path = _metadata_path(meta)
    if path is None:
        return None
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(_storage_dir().resolve()) or not resolved_path.is_file():
        return None
    content_type = meta.get("content_type")
    return resolved_path, content_type if isinstance(content_type, str) else "image/png"


def _iter_meta_files() -> Iterator[tuple[Path, dict]]:
    for mp in _storage_dir().glob("*.json"):
        meta = _read_metadata(mp)
        if meta is None:
            _safe_unlink(mp)
            continue
        yield mp, meta


def cleanup_expired() -> None:
    ttl = SETTINGS.temp_file_ttl_hours * 3600
    now = time.time()
    count = 0
    for mp, meta in _iter_meta_files():
        created_at = _metadata_created_at(meta)
        if created_at is None:
            created_at = 0.0
        if now - created_at > ttl:
            path = _metadata_path(meta)
            if path is not None:
                _safe_unlink(path)
            _safe_unlink(mp)
            count += 1
    if count:
        logger.info("Cleaned up expired temp files", extra={"count": count})


def purge_user(user_id: int) -> None:
    """删除该用户名下的全部临时文件（被遗忘权）。"""
    count = 0
    for mp, meta in _iter_meta_files():
        if meta.get("user_id") == user_id:
            path = _metadata_path(meta)
            if path is not None:
                _safe_unlink(path)
            _safe_unlink(mp)
            count += 1
    if count:
        logger.info("Purged user temp files", extra={"user_id": user_id, "count": count})


def _safe_unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        resolved_path = path.resolve()
        if resolved_path.is_relative_to(_storage_dir().resolve()):
            resolved_path.unlink(missing_ok=True)
