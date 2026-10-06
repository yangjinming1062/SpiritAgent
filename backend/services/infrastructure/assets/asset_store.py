import asyncio
import base64
import hashlib
import hmac
import io
import os
import secrets
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from components import REMOTE_ASSET_DOWNLOAD_MAX_BYTES, SETTINGS, get_file_path, get_logger
from components.asset_paths import parse_companion_asset_path, valid_asset_relative_path
from PIL import Image

from .write_protection import asset_write_context, forget_asset_write, protect_asset_write, user_asset_lock

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


# MPO 是带多图扩展的 JPEG（部分手机照片），首帧可按 JPEG 读取。
_IMAGE_MIME_BY_FORMAT: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


_IMAGE_MIME_BY_EXTENSION = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def image_mime_for_extension(extension: str) -> str | None:
    return _IMAGE_MIME_BY_EXTENSION.get(extension.lower().lstrip("."))


def normalize_asset_reference(reference: str | None) -> str:
    """将响应资产／草稿 URL 还原为校验后的裸路径；非本服务资产返回空串。"""
    if not reference:
        return ""
    try:
        path = unquote(urlsplit(reference.strip()).path)
    except ValueError:
        return ""
    for prefix, bare in (("/api/media/files/", "temp-media/"), ("/api/companion/asset/", "companion-assets/")):
        if path.startswith(prefix):
            path = bare + path.removeprefix(prefix)
            break
    if path.startswith("temp-media/"):
        name = path.removeprefix("temp-media/")
        return path if name and "/" not in name and "\\" not in name and ".." not in name and "\x00" not in name else ""
    return path if parse_companion_asset_path(path) is not None else ""


def resolve_asset_reference(reference: str | None) -> tuple[Path, str] | None:
    bare = normalize_asset_reference(reference)
    if bare.startswith("temp-media/"):
        return get_file_path(bare.removeprefix("temp-media/"))
    parsed = parse_companion_asset_path(bare)
    return resolve_companion_asset_path(*parsed) if parsed is not None else None


def read_asset_data_uri(reference: str | None) -> str | None:
    """在工作线程中读取并编码图片；不可读或非图像资产返回 None。"""
    resolved = resolve_asset_reference(reference)
    if resolved is None or not resolved[1].startswith("image/"):
        return None
    try:
        return build_data_uri(resolved[0].read_bytes(), resolved[1])
    except OSError:
        return None


class UnsupportedImageFormatError(ValueError):
    """图片可以识别，但格式不在 PNG / JPEG / WebP / GIF 范围内。"""


class ImagePixelLimitError(ValueError):
    """Pillow 拒绝解码的超大图片。"""


def validate_image_bytes(data: bytes) -> tuple[bytes, str]:
    """按实际字节校验用户图片并返回其真实 MIME，不采信客户端声明；限制体积并完整解码，拒绝截断或损坏文件。"""
    if not data or len(data) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
        raise ValueError("image size exceeds limit")
    try:
        opened = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        raise ImagePixelLimitError("image dimensions exceed limit") from None
    with opened as image:
        mime = _IMAGE_MIME_BY_FORMAT.get(image.format or "")
        if mime is None:
            raise UnsupportedImageFormatError("unsupported image format")
        image.load()
        if image.format in {"GIF", "MPO"} or getattr(image, "is_animated", False):
            image.seek(0)
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG")
            normalized = output.getvalue()
            if len(normalized) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
                raise ValueError("normalized image size exceeds limit")
            return normalized, "image/png"
    return data, mime


def _assets_root() -> Path:
    return Path(SETTINGS.data_dir) / "companion-assets"


def _signing_key() -> bytes:
    secret = SETTINGS.companion_asset_signing_key
    if secret:
        return secret.encode("utf-8")
    raise RuntimeError(
        "companion_asset_signing_key is empty — lifespan startup should have failed before this point.",
    )


def _sign(user_id: int, filename: str, expires_at: int) -> str:
    msg = f"{user_id}:{filename}:{expires_at}".encode()
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def verify_signed_asset_request(user_id: int, filename: str, expires: int | None, sig: str | None) -> bool:
    if expires is None or sig is None:
        return False
    if expires < int(time.time()):
        return False
    expected = _sign(user_id, filename, int(expires))
    # compare_digest 只接受 ASCII str；按字节比较，非 ASCII 签名返回不匹配而不是抛 TypeError
    return hmac.compare_digest(expected.encode(), sig.encode("utf-8", "surrogatepass"))


def _write_atomic(target: Path, data: bytes) -> None:
    """先写同目录临时文件再原子替换，写入失败或中断都不会在目标路径留下半截文件。"""
    temporary = target.with_name(f".{target.name}.{secrets.token_urlsafe(8)}.tmp")
    try:
        with open(temporary, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def outfit_asset_directory(outfit_id: int) -> str:
    if outfit_id <= 0:
        raise ValueError("invalid outfit ID")
    return f"outfit{outfit_id}"


def pack_asset_directory(outfit_id: int | None, pack_id: int) -> str:
    if pack_id <= 0:
        raise ValueError("invalid pack ID")
    prefix = f"{outfit_asset_directory(outfit_id)}/" if outfit_id is not None else ""
    return f"{prefix}pack{pack_id}"


def scene_asset_directory(scene_id: int) -> str:
    if scene_id <= 0:
        raise ValueError("invalid scene ID")
    return f"scene{scene_id}"


def dated_asset_directory(created_at: datetime, timezone: str) -> str:
    value = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(timezone)).strftime("%Y%m%d")


def _asset_path(user_id: int, directory: str, filename: str) -> str:
    if user_id <= 0 or (directory and not valid_asset_relative_path(directory)):
        raise ValueError("invalid asset directory")
    relative = f"{directory}/{filename}" if directory else filename
    if not valid_asset_relative_path(relative):
        raise ValueError("invalid asset path")
    return f"companion-assets/{user_id}/{relative}"


def _asset_target(user_id: int, relative: str) -> Path:
    if user_id <= 0 or not valid_asset_relative_path(relative):
        raise ValueError("invalid asset path")
    root = _assets_root().resolve()
    user_root = root / str(user_id)
    target = user_root / relative
    # 用户目录自身也不得链接到其他账户；拒绝路径中所有符号链接，读写采用相同边界。
    if any(path.is_symlink() for path in (target, *target.parents) if path != root and path.is_relative_to(root)):
        raise ValueError("asset path contains a symbolic link")
    if not target.resolve().is_relative_to(user_root):
        raise ValueError("asset path escapes user directory")
    return target


def save_companion_asset(data: bytes, *, user_id: int, label: str, ext: str, directory: str) -> str:
    """按显式归属保存资产；label 仅作文件名前缀，不可当查找键使用。"""
    safe_label = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in label)[:48] or "asset"
    if not ext or not ext.isascii() or not ext.isalnum():
        raise ValueError("invalid asset extension")
    filename = f"{safe_label}_{secrets.token_urlsafe(8)}.{ext}"
    bare = _asset_path(user_id, directory, filename)
    target = _asset_target(user_id, bare.split("/", 2)[2])
    target.parent.mkdir(parents=True, exist_ok=True)
    protect_asset_write(bare)
    try:
        _write_atomic(target, data)
    except BaseException:
        forget_asset_write(bare)
        raise
    logger.info("Saved companion asset", extra={"user_id": user_id, "label": label, "size": len(data)})
    return bare


async def save_companion_asset_async(data: bytes, *, user_id: int, label: str, ext: str, directory: str) -> str:
    """在线程写盘；取消时等写盘退出并删除未交接的资产。"""
    async with user_asset_lock(user_id):
        with asset_write_context():
            task = asyncio.create_task(
                asyncio.to_thread(
                    save_companion_asset,
                    data,
                    user_id=user_id,
                    label=label,
                    ext=ext,
                    directory=directory,
                ),
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                result = (await asyncio.gather(task, return_exceptions=True))[0]
                if isinstance(result, str):
                    await asyncio.to_thread(unlink_companion_asset, result)
                raise


def video_job_asset_path(user_id: int, job_id: int, attempt: int, *, generation_id: str, directory: str) -> str:
    """视频任务每次已知提交对应唯一落盘位置，供崩溃后按任务恢复。"""
    if user_id <= 0 or job_id <= 0 or attempt < 0:
        raise ValueError("invalid video job asset key")
    if not _is_generation_id(generation_id):
        raise ValueError("invalid video generation ID")
    return _asset_path(user_id, directory, f"chat_video_job_{job_id}_{generation_id}_a{attempt}.mp4")


def _save_generation_asset(data: bytes, user_id: int, bare_path: str) -> str:
    parsed = parse_companion_asset_path(bare_path)
    if parsed is None or parsed[0] != user_id:
        raise ValueError("invalid generation asset path")
    target = _asset_target(*parsed)
    target.parent.mkdir(parents=True, exist_ok=True)
    protect_asset_write(bare_path)
    try:
        if not target.exists():
            _write_atomic(target, data)
    except BaseException:
        forget_asset_write(bare_path)
        raise
    return bare_path


async def _save_generation_asset_async(data: bytes, user_id: int, bare_path: str) -> str:
    """固定路径由任务先行登记；取消时等原子写盘完成，已落盘结果保留给恢复路径，不当作失败清理。"""
    async with user_asset_lock(user_id):
        with asset_write_context():
            task = asyncio.create_task(asyncio.to_thread(_save_generation_asset, data, user_id, bare_path))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # 写盘自身的失败不能顶替取消向上传播
                await asyncio.gather(task, return_exceptions=True)
                raise


async def save_video_job_asset_async(
    data: bytes,
    *,
    user_id: int,
    job_id: int,
    attempt: int,
    generation_id: str,
    directory: str,
) -> str:
    return await _save_generation_asset_async(
        data,
        user_id,
        video_job_asset_path(user_id, job_id, attempt, generation_id=generation_id, directory=directory),
    )


def _is_generation_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def action_source_asset_path(user_id: int, generation_id: str, attempt: int, ext: str, *, directory: str) -> str:
    if user_id <= 0 or attempt < 0 or ext not in {"mp4", "webm", "mov", "mkv"} or not _is_generation_id(generation_id):
        raise ValueError("invalid action source asset key")
    return _asset_path(user_id, directory, f"action_{generation_id}_a{attempt}.{ext}")


async def save_action_source_asset_async(
    data: bytes,
    *,
    user_id: int,
    generation_id: str,
    attempt: int,
    ext: str,
    directory: str,
) -> str:
    return await _save_generation_asset_async(
        data,
        user_id,
        action_source_asset_path(user_id, generation_id, attempt, ext, directory=directory),
    )


def action_pose_asset_path(user_id: int, generation_id: str, *, directory: str) -> str:
    if user_id <= 0 or not _is_generation_id(generation_id):
        raise ValueError("invalid action pose asset key")
    return _asset_path(user_id, directory, f"action_pose_{generation_id}.png")


async def save_action_pose_asset_async(data: bytes, *, user_id: int, generation_id: str, directory: str) -> str:
    return await _save_generation_asset_async(
        data,
        user_id,
        action_pose_asset_path(user_id, generation_id, directory=directory),
    )


def image_chain_asset_path(
    user_id: int,
    generation_id: str,
    attempt: int,
    slot: int,
    ext: str,
    *,
    directory: str,
) -> str:
    if (
        user_id <= 0
        or attempt < 0
        or slot < 0
        or ext not in {"png", "jpg", "webp", "gif"}
        or not _is_generation_id(generation_id)
    ):
        raise ValueError("invalid image chain asset key")
    return _asset_path(user_id, directory, f"image_{generation_id}_a{attempt}_s{slot}.{ext}")


def scene_wallpaper_asset_path(user_id: int, generation_id: str, *, directory: str) -> str:
    if user_id <= 0 or not _is_generation_id(generation_id):
        raise ValueError("invalid scene wallpaper asset key")
    return _asset_path(user_id, directory, f"scene_wallpaper_{generation_id}.png")


async def save_scene_wallpaper_asset_async(data: bytes, *, user_id: int, generation_id: str, directory: str) -> str:
    return await _save_generation_asset_async(
        data,
        user_id,
        scene_wallpaper_asset_path(user_id, generation_id, directory=directory),
    )


async def save_image_chain_asset_async(
    data: bytes,
    *,
    user_id: int,
    generation_id: str,
    attempt: int,
    slot: int,
    ext: str,
    directory: str,
) -> str:
    return await _save_generation_asset_async(
        data,
        user_id,
        image_chain_asset_path(user_id, generation_id, attempt, slot, ext, directory=directory),
    )


def resolve_companion_asset_path(user_id: int, filename: str) -> tuple[Path, str] | None:
    try:
        resolved = _asset_target(user_id, filename)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file():
        return None
    ext = resolved.suffix.lstrip(".").lower()
    content_type = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
        "json": "application/json",
        "mkv": "video/x-matroska",
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
    return resolved, content_type


def signed_companion_asset_url(storage_path: str) -> str | None:
    """将裸存储路径签名为 /asset 路由 URL；路径非法时返回 None。调用方不要缓存——签名短时过期，每次列表刷新都应重新签名。"""
    parsed = parse_companion_asset_path(storage_path)
    if parsed is None:
        return None
    user_id, filename = parsed
    expires_at = int(time.time()) + _ASSET_URL_TTL_SECONDS
    qs = urlencode({"expires": expires_at, "sig": _sign(user_id, filename, expires_at)})
    return f"/api/companion/asset/{user_id}/{filename}?{qs}"


def client_asset_url(storage_path: str) -> str:
    """裸资产路径改写为客户端可鉴权加载的 /api/companion/asset/ 路径；其他形态原样返回。"""
    if storage_path.startswith("companion-assets/"):
        return "/api/companion/asset/" + storage_path.removeprefix("companion-assets/")
    return storage_path


def delete_user_assets(user_id: int) -> None:
    """删除该用户的整个资产目录（被遗忘权）；文件系统错误向上抛出。"""
    user_dir = _assets_root() / str(user_id)
    if user_dir.exists():
        shutil.rmtree(user_dir)
        logger.info("Deleted user asset directory", extra={"user_id": user_id})


def unlink_companion_asset(storage_path: str | None) -> None:
    """尽力删除裸存储路径对应文件；路径非法或文件缺失时忽略，删除失败只记日志。"""
    parsed = parse_companion_asset_path(storage_path)
    if parsed is None:
        return
    forget_asset_write(storage_path or "")
    resolved = resolve_companion_asset_path(*parsed)
    if resolved is None:
        return
    try:
        resolved[0].unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to delete companion asset", extra={"path": storage_path}, exc_info=True)


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
