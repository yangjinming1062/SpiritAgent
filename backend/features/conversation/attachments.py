"""会话音视频附件：落盘、配额、引用改写、派生复制与视频内联。"""

import asyncio
import base64
import contextlib
import json
import re
import secrets
import shutil
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote, urlsplit

from infrastructure.async_io import cancel_and_wait, run_blocking
from infrastructure.attachments import session_dir
from infrastructure.config import SETTINGS
from infrastructure.constants import ATTACHMENT_VIDEO_EXTENSIONS, VIDEO_INLINE_MAX_PER_REQUEST
from infrastructure.logger import get_logger
from infrastructure.text import safe_json_loads
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.asset_bindings import commit_conversation
from features.conversation.models import Message

logger = get_logger(__name__)

# 附件 URL 前缀；与 entrypoints/http/routes/media.py 保持一致（docs/PROTOCOL.md 契约）。
VIDEO_URL_PREFIX = "/api/media/videos"
AUDIO_URL_PREFIX = "/api/media/audios"

# file_id 即磁盘文件名：token_urlsafe 主体 + 白名单扩展名；URL 直接携带它。
_VIDEO_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}\.(mp4|mov)$")
_AUDIO_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}\.(wav|mp3|ogg|m4a|flac|webm)$")
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}\.(mp4|mov|wav|mp3|ogg|m4a|flac|webm)$")

_VIDEO_MIME_BY_EXT = {".mp4": "video/mp4", ".mov": "video/quicktime"}
_AUDIO_MIME_BY_EXT = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}

# 被清理/降级时的占位文本；渲染层照常显示，LLM 侧同样是合法 input_text。
VIDEO_PRUNED_TEXT = "[视频已清理]"
VIDEO_DEGRADED_TEXT = "[video]"
AUDIO_PRUNED_TEXT = "[音频已清理]"


def attachment_video_url(session_id: str, file_id: str) -> str:
    """附件的对外 URL：公网模式拼 ``public_base_url`` 绝对地址（供应商可拉取），本地模式返回相对路径。"""
    path = f"{VIDEO_URL_PREFIX}/{session_id}/{quote(file_id, safe='')}"
    base = SETTINGS.public_base_url.strip().rstrip("/")
    return f"{base}{path}" if base else path


def video_file_id_from_url(video_url: str, session_id: str) -> str | None:
    """解析本服务的相对或公网附件 URL；跨会话、第三方或形态非法时返回 None。"""
    relative_prefix = f"{VIDEO_URL_PREFIX}/{session_id}/"
    if video_url.startswith(relative_prefix):
        tail = video_url[len(relative_prefix) :]
    else:
        base = SETTINGS.public_base_url.strip().rstrip("/")
        absolute_prefix = f"{base}{relative_prefix}" if base else ""
        if not absolute_prefix or not video_url.startswith(absolute_prefix):
            return None
        tail = video_url[len(absolute_prefix) :]
    return tail if _VIDEO_FILE_NAME_RE.fullmatch(tail) else None


def attachment_audio_url(session_id: str, file_id: str) -> str:
    return f"{AUDIO_URL_PREFIX}/{session_id}/{quote(file_id, safe='')}"


def audio_file_id_from_url(url: str, session_id: str) -> str | None:
    prefix = f"{AUDIO_URL_PREFIX}/{session_id}/"
    tail = url.removeprefix(prefix) if url.startswith(prefix) else ""
    return tail if _AUDIO_FILE_NAME_RE.fullmatch(tail) else None


def audio_mime_for_ext(ext: str) -> str:
    return _AUDIO_MIME_BY_EXT.get(ext.lower(), "application/octet-stream")


def resolve_audio_file(session_id: str, file_id: str) -> Path | None:
    path = _media_file_path(session_id, file_id) if _AUDIO_FILE_NAME_RE.fullmatch(file_id) else None
    return path if path is not None and path.is_file() else None


def _stored_media_file_id(media_url: str, session_id: str) -> str | None:
    """识别已入库消息引用的本会话音视频附件：只在 URL 路径中定位本会话标记（``public_base_url`` 可带路径前缀），不依赖当前配置，公网地址变更后旧引用仍能清理与改写；跨会话或形态非法返回 None。提交校验与供应商直通须用严格的媒体 URL 解析函数。"""
    try:
        path = urlsplit(media_url).path
    except ValueError:
        return None
    for prefix, pattern in ((VIDEO_URL_PREFIX, _VIDEO_FILE_NAME_RE), (AUDIO_URL_PREFIX, _AUDIO_FILE_NAME_RE)):
        marker = f"{prefix}/{session_id}/"
        start = path.find(marker)
        if start >= 0:
            tail = path[start + len(marker) :]
            return tail if pattern.fullmatch(tail) else None
    return None


def video_mime_for_ext(ext: str) -> str:
    return _VIDEO_MIME_BY_EXT.get(ext.lower(), "application/octet-stream")


def _media_file_path(session_id: str, file_id: str) -> Path | None:
    """把 (session_id, file_id) 解析到会话目录内的文件路径；形态非法或越界返回 None。"""
    if not _FILE_NAME_RE.fullmatch(file_id):
        return None
    try:
        root = session_dir(session_id).resolve()
    except ValueError:
        return None
    target = (root / file_id).resolve()
    if not target.is_relative_to(root):
        return None
    return target


def resolve_video_file(session_id: str, file_id: str) -> Path | None:
    """GET 服务端点用：路径存在且仍在目录内才放行。"""
    path = _media_file_path(session_id, file_id) if _VIDEO_FILE_NAME_RE.fullmatch(file_id) else None
    if path is None or not path.is_file():
        return None
    return path


def _save_attachment(session_id: str, source: BinaryIO, ext: str, max_bytes: int) -> tuple[str, int]:
    """从上传临时文件分块复制；失败删除未交接文件。"""
    target_dir = session_dir(session_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_id = f"{secrets.token_urlsafe(16)}{ext.lower()}"
    target = target_dir / file_id
    size = 0
    try:
        source.seek(0)
        with target.open("xb") as output:
            while chunk := source.read(256 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("attachment size exceeds limit")
                output.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return file_id, size


def save_video_attachment(session_id: str, source: BinaryIO, ext: str, max_bytes: int) -> tuple[str, int]:
    if ext.lower() not in ATTACHMENT_VIDEO_EXTENSIONS:
        raise ValueError("unsupported video extension")
    return _save_attachment(session_id, source, ext, max_bytes)


def save_audio_attachment(session_id: str, source: BinaryIO, ext: str, max_bytes: int) -> tuple[str, int]:
    if ext.lower() not in _AUDIO_MIME_BY_EXT:
        raise ValueError("unsupported audio extension")
    return _save_attachment(session_id, source, ext, max_bytes)


def _rewrite_parts(parts: list, file_ids: set[str], *, session_id: str) -> tuple[list, bool]:
    """把引用了 ``file_ids`` 的音视频 part 替换为清理占位文本；返回 (新 parts, 是否有改动)。按路径识别本会话附件，拒掉跨会话 / 形态非法的 URL，防止 stale DB 行把任意路径污染到 victim 集合比对中。"""
    changed = False
    out: list = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") in {"input_video", "input_audio"}:
            url = str(part.get("video_url") or part.get("audio_url") or "")
            file_id = _stored_media_file_id(url, session_id)
            if file_id is None:
                out.append(part)
                continue
            if file_id in file_ids:
                out.append(
                    {
                        "type": "input_text",
                        "text": part.get("transcript")
                        or (AUDIO_PRUNED_TEXT if part["type"] == "input_audio" else VIDEO_PRUNED_TEXT),
                    },
                )
                changed = True
                continue
        out.append(part)
    return out, changed


def _media_messages(conversation_id: int) -> list[ColumnElement[bool]]:
    """会话内可能引用音视频附件的多模态用户行。"""
    return [
        Message.conversation_id == conversation_id,
        Message.role == "user",
        Message.content_type == "multimodal_v1",
        Message.content.like('%"input_video"%') | Message.content.like('%"input_audio"%'),
    ]


def _load_parts(content: str | None) -> list | None:
    """解析多模态 parts；非 list 返回 None，该行保持原状。"""
    parts = safe_json_loads(content or "[]", default=[])
    return parts if isinstance(parts, list) else None


def _referenced_file_ids(parts: list, session_id: str) -> set[str]:
    return {
        file_id
        for part in parts
        if isinstance(part, dict)
        and part.get("type") in {"input_video", "input_audio"}
        and (file_id := _stored_media_file_id(str(part.get("video_url") or part.get("audio_url") or ""), session_id))
        is not None
    }


def _parse_message_parts(messages: Sequence[Message]) -> list[tuple[Message, list]]:
    """逐行解析 parts 并跳过非 list 行；收集与改写共用同一份解析结果。"""
    return [(message, parts) for message in messages if (parts := _load_parts(message.content)) is not None]


def _collect_message_parts(messages: Sequence[Message], session_id: str) -> tuple[list[tuple[Message, list]], set[str]]:
    parsed = _parse_message_parts(messages)
    file_ids: set[str] = set()
    for _message, parts in parsed:
        file_ids |= _referenced_file_ids(parts, session_id)
    return parsed, file_ids


def _rewrite_rows(parsed: Sequence[tuple[Message, list]], file_ids: set[str], session_id: str) -> int:
    """把引用了被删文件的行改写为占位文本；返回改写行数。不内部 commit。"""
    rewritten = 0
    for message, parts in parsed:
        new_parts, changed = _rewrite_parts(parts, file_ids, session_id=session_id)
        if changed:
            message.content = json.dumps(new_parts, ensure_ascii=False)
            rewritten += 1
    return rewritten


def _unlink_files(paths: list[Path]) -> int:
    removed = 0
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink()
            removed += 1
    return removed


async def enforce_session_quota(db: AsyncSession, session_id: str, incoming_bytes: int) -> None:
    """写盘前保证会话目录余量：存量+本次超配额时从最旧文件开始剔除；引用行先改写并提交，再删除文件。"""
    root = session_dir(session_id)
    if not root.exists():
        return

    def _overflow_victims() -> list[Path]:
        entries: list[tuple[float, int, Path]] = []
        total = incoming_bytes
        for p in root.iterdir():
            try:
                if not p.is_file():
                    continue
                stat = p.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, p))
            total += stat.st_size
        if total <= SETTINGS.attachment_session_quota_bytes:
            return []
        victims: list[Path] = []
        for _mtime, size, p in sorted(entries):  # 最旧在前
            if total <= SETTINGS.attachment_session_quota_bytes:
                break
            total -= size
            victims.append(p)
        return victims

    victims = await asyncio.to_thread(_overflow_victims)
    if not victims:
        return
    file_ids = {p.name for p in victims}
    # populate_existing 固化列级查询恒取 DB 新值的改写基准。
    messages = (
        await db.scalars(
            select(Message).where(*_media_messages(int(session_id))).execution_options(populate_existing=True),
        )
    ).all()
    parsed = _parse_message_parts(messages)
    rewritten = _rewrite_rows(parsed, file_ids, session_id)
    if rewritten:
        await commit_conversation(db)
    await asyncio.to_thread(_unlink_files, victims)
    logger.info(
        "session media quota eviction",
        extra={
            "session_id": session_id,
            "evicted": len(file_ids),
            "rewritten_rows": rewritten,
            "incoming_bytes": incoming_bytes,
        },
    )


async def prune_media_in_range(
    db: AsyncSession,
    conversation_id: int,
    *,
    lo: int = 0,
    hi: int | None = None,
) -> None:
    """清理 ``[lo, hi)`` 区间用户行引用的音视频文件并改写 part。摘要按实际覆盖范围清理，历史撤回按删除范围清理；区间外仍有引用的文件保留，区间内引用改写为占位。有文件要删时先提交当前事务中的改写再删除文件，提交失败不会留下死链。"""
    session_id = str(conversation_id)
    conditions = [*_media_messages(conversation_id), Message.id >= lo]
    if hi is not None:
        conditions.append(Message.id < hi)
    # populate_existing 固化列级查询恒取 DB 新值的改写基准。
    messages = (await db.scalars(select(Message).where(*conditions).execution_options(populate_existing=True))).all()
    parsed, file_ids = _collect_message_parts(messages, session_id)
    if not file_ids:
        return
    outside = (Message.id < lo) | (Message.id >= hi) if hi is not None else Message.id < lo
    for content in await db.scalars(select(Message.content).where(*_media_messages(conversation_id), outside)):
        parts = _load_parts(content)
        if parts is not None:
            file_ids -= _referenced_file_ids(parts, session_id)
    if not file_ids:
        return
    rewritten = _rewrite_rows(parsed, file_ids, session_id)
    await commit_conversation(db)
    root = session_dir(session_id).resolve()
    targets = [target for file_id in file_ids if (target := (root / file_id).resolve()).is_relative_to(root)]
    removed = await asyncio.to_thread(_unlink_files, targets)
    logger.info(
        "session media prune",
        extra={
            "conversation_id": conversation_id,
            "lo": lo,
            "hi": hi,
            "removed_files": removed,
            "rewritten_rows": rewritten,
        },
    )


def _copy_media_files(
    source_session_id: str,
    target_session_id: str,
    file_ids: set[str],
    stop: threading.Event,
) -> set[str]:
    """把源会话的附件复制进派生会话目录，保留 mtime 以延续配额剔除顺序；返回已复制的文件，源文件已不存在的不在其中。stop 置位后在文件之间中止。其他 I/O 错误向上抛出，已复制的文件随派生会话目录由调用方清理。"""
    target_dir = session_dir(target_session_id)
    copied: set[str] = set()
    for file_id in file_ids:
        if stop.is_set():
            break
        source = _media_file_path(source_session_id, file_id)
        if source is None or not source.is_file():
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):  # 与源会话的清理并发
            shutil.copy2(source, target_dir / file_id)
            copied.add(file_id)
    return copied


def _retarget_parts(parts: list, copied: set[str], *, source_session_id: str, target_session_id: str) -> list:
    """把引用源会话附件的音视频 part 改指派生会话的副本；没有副本（源文件已清理）的替换为清理占位。"""
    out: list = []
    for part in parts:
        file_id = (
            _stored_media_file_id(str(part.get("video_url") or part.get("audio_url") or ""), source_session_id)
            if isinstance(part, dict) and part.get("type") in {"input_video", "input_audio"}
            else None
        )
        if file_id is None:
            out.append(part)
        elif file_id in copied:
            key = "audio_url" if part["type"] == "input_audio" else "video_url"
            url = (
                attachment_audio_url(target_session_id, file_id)
                if key == "audio_url"
                else attachment_video_url(target_session_id, file_id)
            )
            out.append({**part, key: url})
        else:
            out.append(
                {
                    "type": "input_text",
                    "text": (part.get("transcript") or AUDIO_PRUNED_TEXT)
                    if part["type"] == "input_audio"
                    else VIDEO_PRUNED_TEXT,
                },
            )
    return out


async def copy_forked_media_attachments(db: AsyncSession, source_session_id: str, target_session_id: str) -> int:
    """派生会话复制的用户行仍引用源会话的音视频附件：把被引用的文件复制进派生会话目录并改写引用，派生历史不再依赖源会话的清理与删除；源文件已不存在的引用改写为清理占位。返回改写的消息行数。失败或被取消时调用方须丢弃派生会话及其附件目录。"""
    messages = (await db.scalars(select(Message).where(*_media_messages(int(target_session_id))))).all()
    parsed, file_ids = _collect_message_parts(messages, source_session_id)
    if not file_ids:
        return 0
    stop = threading.Event()
    copy_task = asyncio.create_task(
        run_blocking(_copy_media_files, source_session_id, target_session_id, file_ids, stop),
    )
    try:
        copied = await asyncio.shield(copy_task)
    except asyncio.CancelledError:
        # 线程无法中断：通知它在文件之间停止并等其退出，调用方随后撤销派生会话时目录才不会被它重建。
        stop.set()
        await cancel_and_wait([copy_task])
        raise
    rewritten = 0
    for message, parts in parsed:
        new_parts = _retarget_parts(
            parts,
            copied,
            source_session_id=source_session_id,
            target_session_id=target_session_id,
        )
        if new_parts != parts:
            message.content = json.dumps(new_parts, ensure_ascii=False)
            rewritten += 1
    await commit_conversation(db)
    return rewritten


def _session_video_exists(url: str, session_id: str) -> bool:
    """URL 是本会话的附件 URL 且文件仍在。"""
    file_id = video_file_id_from_url(url, session_id)
    return file_id is not None and resolve_video_file(session_id, file_id) is not None


def _video_data_url(path: Path) -> str:
    """读取视频并编码为 data URL；读取、编码与拼接在一次线程切换内完成，由调用方放到工作线程执行。"""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{video_mime_for_ext(path.suffix)};base64,{encoded}"


async def inline_video_parts(items: list, *, expected_session_id: str | None = None) -> list:
    """构造供应商请求前的最后一步：把最近的相对 URL ``input_video`` 内联为 data URL。从新到旧分配 ``VIDEO_INLINE_MAX_PER_REQUEST`` 个名额；超出、文件缺失或 URL 指向非 ``expected_session_id`` 会话的降级为 ``[video]`` 占位。公网绝对 URL 在文件仍存在时原样直通且不占名额，否则同样降级。仅修改 dict 项的 list content，其他 item 形状原样保留。"""
    budget = VIDEO_INLINE_MAX_PER_REQUEST
    out_items: list = []
    for item in reversed(items):
        if not (isinstance(item, dict) and isinstance(item.get("content"), list)):
            out_items.append(item)
            continue
        new_parts: list = []
        for part in item["content"]:
            if not (isinstance(part, dict) and part.get("type") == "input_video"):
                new_parts.append(part)
                continue
            url = str(part.get("video_url") or "")
            if url.startswith(("http://", "https://")):
                new_parts.append(
                    part
                    if expected_session_id is None or _session_video_exists(url, expected_session_id)
                    else {"type": "input_text", "text": VIDEO_DEGRADED_TEXT},
                )
                continue
            inlined = None
            if budget > 0 and expected_session_id:
                file_id = video_file_id_from_url(url, expected_session_id)
                path = _media_file_path(expected_session_id, file_id) if file_id else None
                if path is not None and path.is_file():
                    try:
                        data_url = await asyncio.to_thread(_video_data_url, path)
                        inlined = {"type": "input_video", "video_url": data_url}
                        budget -= 1
                    except OSError:
                        inlined = None
            new_parts.append(inlined if inlined is not None else {"type": "input_text", "text": VIDEO_DEGRADED_TEXT})
        out_items.append({**item, "content": new_parts})
    out_items.reverse()
    return out_items
