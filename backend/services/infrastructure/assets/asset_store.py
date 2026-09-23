import asyncio
import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from components import SETTINGS, get_logger

logger = get_logger(__name__)

# 仅 5 分钟：桌面端本就频繁重新拉取，短 TTL 降低链接泄露风险
_ASSET_URL_TTL_SECONDS = 300
_MEDIA_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"ID3", "mp3"),
    (b"OggS", "ogg"),
    (b"fLaC", "flac"),
    (b"\x1a\x45\xdf\xa3", "webm"),
)


def build_data_uri(data: bytes, content_type: str | None = None) -> str:
    """把图片字节编码为 data URI，使供应商内联读取种子图，无需后端可公网访问。"""
    mime = (content_type or "image/png").split(";")[0].strip().lower() or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _assets_root() -> Path:
    return Path(SETTINGS.data_dir) / "companion-assets"


def _signing_key() -> bytes:
    secret = getattr(SETTINGS, "companion_asset_signing_key", None)
    if secret:
        return secret.encode("utf-8")
    raise RuntimeError(
        "companion_asset_signing_key is empty — lifespan startup should have failed before this point.",
    )


def _sign(user_id: int, filename: str, expires_at: int) -> str:
    msg = f"{user_id}:{filename}:{expires_at}".encode()
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def build_signed_asset_url(user_id: int, filename: str, *, ttl_seconds: int = _ASSET_URL_TTL_SECONDS) -> str:
    """签发资产 URL；调用方不要缓存——5 分钟即过期，每次列表刷新都应重新签名。"""
    expires_at = int(time.time()) + ttl_seconds
    sig = _sign(user_id, filename, expires_at)
    qs = urlencode({"expires": expires_at, "sig": sig})
    return f"/api/companion/asset/{user_id}/{filename}?{qs}"


def verify_signed_asset_request(user_id: int, filename: str, expires: int | None, sig: str | None) -> bool:
    if expires is None or sig is None:
        return False
    if int(expires) < int(time.time()):
        return False
    expected = _sign(user_id, filename, int(expires))
    return hmac.compare_digest(expected, sig)


def _sign_avatar(filename: str, expires_at: int) -> str:
    msg = f"avatar:{filename}:{expires_at}".encode()
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def build_signed_avatar_url(file_id: str, ext: str, *, ttl_seconds: int = _ASSET_URL_TTL_SECONDS) -> str:
    expires_at = int(time.time()) + ttl_seconds
    sig = _sign_avatar(f"{file_id}.{ext}", expires_at)
    qs = urlencode({"expires": expires_at, "sig": sig})
    return f"/api/companion/avatar/file/{file_id}.{ext}?{qs}"


def verify_signed_avatar_request(filename: str, expires: int | None, sig: str | None) -> bool:
    if expires is None or sig is None:
        return False
    if int(expires) < int(time.time()):
        return False
    expected = _sign_avatar(filename, int(expires))
    return hmac.compare_digest(expected, sig)


def save_companion_asset(data: bytes, *, user_id: int, label: str, ext: str) -> str:
    """保存资产并返回裸存储路径；label 仅作文件名前缀，不可当查找键使用。"""
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:48] or "asset"
    user_dir = _assets_root() / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(8)
    filename = f"{safe_label}_{token}.{ext}"
    filepath = user_dir / filename
    with open(filepath, "wb") as f:
        f.write(data)
    logger.info("Saved companion asset", extra={"user_id": user_id, "label": label, "size": len(data)})
    return f"companion-assets/{user_id}/{filename}"


async def save_companion_asset_async(data: bytes, *, user_id: int, label: str, ext: str) -> str:
    """在线程写盘；取消时等写盘退出并删除未交接的资产。"""
    task = asyncio.create_task(asyncio.to_thread(save_companion_asset, data, user_id=user_id, label=label, ext=ext))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        if isinstance(result, str):
            await asyncio.to_thread(unlink_companion_asset, result)
        raise


def video_job_asset_path(user_id: int, job_id: int, attempt: int) -> str:
    """视频任务每次已知提交对应唯一落盘位置，供崩溃后按任务恢复。"""
    if user_id <= 0 or job_id <= 0 or attempt < 0:
        raise ValueError("invalid video job asset key")
    return f"companion-assets/{user_id}/chat_video_job_{job_id}_a{attempt}.mp4"


def _save_video_job_asset(data: bytes, user_id: int, job_id: int, attempt: int) -> str:
    bare_path = video_job_asset_path(user_id, job_id, attempt)
    user_dir = _assets_root() / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    target = user_dir / bare_path.rsplit("/", 1)[-1]
    if target.exists():
        return bare_path
    temporary = user_dir / f".{target.name}.{secrets.token_urlsafe(8)}.tmp"
    try:
        with open(temporary, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return bare_path


async def save_video_job_asset_async(data: bytes, *, user_id: int, job_id: int, attempt: int) -> str:
    """取消时等原子写盘完成；已落盘结果保留给恢复路径，不当作失败清理。"""
    task = asyncio.create_task(asyncio.to_thread(_save_video_job_asset, data, user_id, job_id, attempt))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def resolve_companion_asset_path(user_id: int, filename: str) -> tuple[Path, str] | None:
    name = Path(filename).name
    if "/" in name or "\\" in name or ".." in name:
        return None
    filepath = _assets_root() / str(user_id) / name
    if not filepath.exists():
        return None
    ext = filepath.suffix.lstrip(".").lower()
    content_type = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "mp4": "video/mp4",
        "webm": "video/webm",
        "mov": "video/quicktime",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "flac": "audio/flac",
    }.get(ext, "application/octet-stream")
    return filepath, content_type


def parse_companion_asset_path(storage_path: str | None) -> tuple[int, str] | None:
    """拆分裸存储路径为 (uid, filename)；该结构不允许子目录，多余斜杠会错配导致签名 URL 404。"""
    if not storage_path or not storage_path.startswith("companion-assets/"):
        return None
    parts = storage_path.split("/", 2)
    if len(parts) != 3 or "/" in parts[2] or "\\" in parts[2]:
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


def signed_companion_asset_url(storage_path: str) -> str | None:
    """将裸存储路径签名为 /asset 路由 URL；路径非法时返回 None。"""
    parsed = parse_companion_asset_path(storage_path)
    if parsed is None:
        return None
    return build_signed_asset_url(*parsed)


def client_asset_url(storage_path: str) -> str:
    """裸资产路径改写为客户端可鉴权加载的 /api/companion/asset/ 路径；其他形态原样返回。"""
    if storage_path.startswith("companion-assets/"):
        return "/api/companion/asset/" + storage_path.removeprefix("companion-assets/")
    return storage_path


def unlink_companion_asset(storage_path: str | None) -> Path | None:
    """尽力删除裸存储路径对应文件，返回被删路径；路径非法或文件缺失时返回 None。"""
    parsed = parse_companion_asset_path(storage_path)
    if parsed is None:
        return None
    resolved = resolve_companion_asset_path(*parsed)
    if resolved is None:
        return None
    try:
        resolved[0].unlink(missing_ok=True)
        return resolved[0]
    except OSError:
        return None


def sniff_media_ext(data: bytes) -> str | None:
    """识别图片、视频与音频的文件签名；不保证文件完整或可解码。"""
    for magic, ext in _MEDIA_MAGIC:
        if data.startswith(magic):
            return ext
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if len(data) >= 16 and data[4:8] == b"ftyp":
        if data[8:12] in (b"M4A ", b"M4B ", b"M4P "):
            return "m4a"
        if data[8:12] == b"qt  ":
            return "mov"
        return "mp4"
    if len(data) >= 4 and data[0] == 0xFF:
        if data[1] & 0xF6 == 0xF0:
            return "aac"
        if data[1] & 0xE0 == 0xE0 and data[1] & 0x06:
            return "mp3"
    return None


def compute_file_sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(256 * 1024):
            h.update(chunk)
    return h.hexdigest()
