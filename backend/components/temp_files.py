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
_MEDIA_EXTENSIONS = ("jpg", "png", "jpeg", "webp", "glb", "wav", "mp3")


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


def save_file(
    data: bytes,
    session_id: str,
    content_type: str,
    ext: str,
    *,
    meta_marker: str | None = None,
) -> tuple[str, str]:
    """保存字节到 temp 存储，返回 (file_id, public_url)；meta_marker 是所有权/身份标签（如 ``"preview:{user_id}"``），写入 meta 后由删除路径校验以拒绝跨 owner 删除；meta 写失败时 unlink 数据文件，避免无 TTL 跟踪的孤儿。"""
    file_id = secrets.token_urlsafe(16)
    filepath = _media_path(file_id, ext)

    with open(filepath, "wb") as f:
        f.write(data)

    meta = {
        "path": str(filepath),
        "session_id": session_id,
        "created_at": time.time(),
        "content_type": content_type,
        "size": len(data),
    }
    if meta_marker is not None:
        meta["marker"] = meta_marker
    try:
        with open(_meta_path(file_id), "w") as f:
            json.dump(meta, f)
    except OSError as exc:
        _safe_unlink(filepath)
        logger.warning("temp meta write failed; orphan removed", extra={"file_id": file_id, "error": str(exc)})
        raise

    public_url = _build_public_url(file_id)
    logger.info(
        "Temp file saved",
        extra={"file_id": file_id, "size": len(data), "session_id": session_id, "marker": meta_marker},
    )
    return file_id, public_url


def _build_public_url(file_id: str) -> str:
    return f"/api/media/files/{file_id}"


def get_file_path(file_id: str) -> tuple[Path, str] | None:
    """按 ID 取文件路径与 content_type；未找到/已过期返 None。"""
    if not _valid_file_id(file_id):
        return None
    mp = _meta_path(file_id)
    if mp.exists():
        meta = _read_metadata(mp)
        if meta is None:
            return None
        created_at = _metadata_created_at(meta)
        if created_at is None or time.time() - created_at > SETTINGS.temp_file_ttl_hours * 3600:
            return None
        raw_path = _metadata_path(meta)
        if raw_path is None:
            return None
        path = raw_path if raw_path.is_absolute() else _storage_dir() / raw_path
        resolved_path = path.resolve()
        if resolved_path.is_relative_to(_storage_dir().resolve()) and resolved_path.is_file():
            content_type = meta.get("content_type", "image/png")
            return resolved_path, content_type if isinstance(content_type, str) else "image/png"
        return None

    storage_dir = _storage_dir().resolve()
    for ext in _MEDIA_EXTENSIONS:
        candidate = _storage_dir() / f"{file_id}.{ext}"
        try:
            resolved_path = candidate.resolve()
            stat_result = resolved_path.stat()
        except OSError:
            continue
        if not resolved_path.is_relative_to(storage_dir):
            continue
        if time.time() - stat_result.st_mtime > SETTINGS.temp_file_ttl_hours * 3600:
            _safe_unlink(resolved_path)
            continue
        if resolved_path.is_file():
            content_type = (
                "image/jpeg"
                if ext in ("jpg", "jpeg")
                else ("image/png" if ext == "png" else "application/octet-stream")
            )
            return resolved_path, content_type

    return None


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


def gc_session(session_id: str) -> None:
    count = 0
    for mp, meta in _iter_meta_files():
        if meta.get("session_id") == session_id:
            path = _metadata_path(meta)
            if path is not None:
                _safe_unlink(path)
            _safe_unlink(mp)
            count += 1
    if count:
        logger.info("Cleaned up session temp files", extra={"session_id": session_id, "count": count})


def _safe_unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        resolved_path = path.resolve()
        if resolved_path.is_relative_to(_storage_dir().resolve()):
            resolved_path.unlink(missing_ok=True)
