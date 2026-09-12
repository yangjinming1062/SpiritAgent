"""聊天视频附件的后端生命周期：URL 构建、落盘、滚动配额、检查点清理与请求时内联。

上传走 HTTP（WS 单帧装不下 base64），文件落 ``desktop-attachments/{session_id}/``，
``prompt.submit`` 引用后端 URL；本地模式在构造供应商请求时把最近的 URL 内联为 data URL，
公网模式（``public_base_url`` 非空）直接把绝对 URL 交给供应商自行拉取。
配额超限与检查点清理都会把被删文件所属消息行的 ``input_video`` part 改写为
``[视频已清理]`` 文本，保证 DB、渲染与 LLM 上下文三方一致，不残留死链 URL。
"""

import asyncio
import base64
import contextlib
import json
import re
import secrets
from pathlib import Path
from urllib.parse import quote

from components import (
    ATTACHMENT_SESSION_QUOTA_BYTES,
    ATTACHMENT_VIDEO_EXTENSIONS,
    SETTINGS,
    VIDEO_INLINE_MAX_PER_REQUEST,
    get_logger,
    safe_json_loads,
    session_dir,
)
from modules.conversation import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# 附件 URL 前缀；与 api/v1/media.py 的路由保持一致（docs/PROTOCOL.md 契约）。
VIDEO_URL_PREFIX = "/api/media/videos"

# file_id 即磁盘文件名：token_urlsafe 主体 + 白名单扩展名；URL 直接携带它。
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}\.(mp4|mov)$")

_VIDEO_MIME_BY_EXT = {".mp4": "video/mp4", ".mov": "video/quicktime"}

# 被清理/降级时的占位文本；渲染层照常显示，LLM 侧同样是合法 input_text。
VIDEO_PRUNED_TEXT = "[视频已清理]"
VIDEO_DEGRADED_TEXT = "[video]"


def attachment_video_url(session_id: str, file_id: str) -> str:
    """附件的对外 URL：公网模式拼 ``public_base_url`` 绝对地址（供应商可拉取），本地模式返回相对路径。"""
    path = f"{VIDEO_URL_PREFIX}/{session_id}/{quote(file_id, safe='')}"
    base = SETTINGS.public_base_url.strip().rstrip("/")
    return f"{base}{path}" if base else path


def _file_id_from_url(video_url: str, session_id: str) -> str | None:
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
    return tail if _FILE_NAME_RE.fullmatch(tail) else None


def video_mime_for_ext(ext: str) -> str:
    return _VIDEO_MIME_BY_EXT.get(ext.lower(), "application/octet-stream")


def _video_file_path(session_id: str, file_id: str) -> Path | None:
    """把 (session_id, file_id) 解析到会话目录内的文件路径；形态非法或越界返回 None。"""
    if not _FILE_NAME_RE.fullmatch(file_id):
        return None
    root = session_dir(SETTINGS.data_dir, session_id).resolve()
    target = (root / file_id).resolve()
    if not target.is_relative_to(root):
        return None
    return target


def resolve_video_file(session_id: str, file_id: str) -> Path | None:
    """GET 服务端点用：路径存在且仍在目录内才放行。"""
    path = _video_file_path(session_id, file_id)
    if path is None or not path.is_file():
        return None
    return path


def save_video_attachment(session_id: str, data: bytes, ext: str) -> tuple[str, int]:
    """落盘视频附件，返回 (file_id, size)。扩展名白名单在此兜底；配额剔除由调用方先行。"""
    if ext.lower() not in ATTACHMENT_VIDEO_EXTENSIONS:
        raise ValueError(f"unsupported video extension: {ext!r}")
    target_dir = session_dir(SETTINGS.data_dir, session_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_id = f"{secrets.token_urlsafe(16)}{ext.lower()}"
    (target_dir / file_id).write_bytes(data)
    return file_id, len(data)


def _rewrite_parts(parts: list, file_ids: set[str], *, session_id: str | None = None) -> tuple[list, bool]:
    """把引用了 ``file_ids`` 的 input_video part 替换为清理占位文本；返回 (新 parts, 是否有改动)。

    ``session_id`` 传入时拒掉跨会话 / 形态非法的 URL，防止 stale DB 行把任意路径污染到 victim 集合比对中。
    """
    changed = False
    out: list = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "input_video":
            url = str(part.get("video_url") or "")
            if session_id is not None:
                file_id = _file_id_from_url(url, session_id)
                if file_id is None:
                    out.append(part)
                    continue
            else:
                file_id = url.rsplit("/", 1)[-1]
            if file_id in file_ids:
                out.append({"type": "input_text", "text": VIDEO_PRUNED_TEXT})
                changed = True
                continue
        out.append(part)
    return out, changed


async def _rewrite_rows_referencing(
    db: AsyncSession,
    session_id: str,
    file_ids: set[str],
    *,
    lo: int = 0,
    hi: int | None = None,
) -> int:
    """把引用了被删文件的多模态用户行改写为占位文本；返回改写行数（session_id 即会话数字 id）。不内部 commit。"""
    if not file_ids:
        return 0
    conditions = [
        Message.conversation_id == int(session_id),
        Message.id >= lo,
        Message.role == "user",
        Message.content_type == "multimodal_v1",
        Message.content.like('%"input_video"%'),
    ]
    if hi is not None:
        conditions.append(Message.id < hi)
    rows = (await db.execute(select(Message.id, Message.content).where(*conditions))).all()
    rewritten = 0
    for message_id, content in rows:
        parts = safe_json_loads(content, default=[])
        if not isinstance(parts, list):
            continue
        new_parts, changed = _rewrite_parts(parts, file_ids, session_id=session_id)
        if changed:
            message = await db.get(Message, message_id)
            if message is not None:
                message.content = json.dumps(new_parts, ensure_ascii=False)
                rewritten += 1
    return rewritten


async def enforce_session_quota(db: AsyncSession, session_id: str, incoming_bytes: int) -> None:
    """写盘前保证会话目录余量：存量+本次超配额时从最旧文件开始剔除并改写引用行。"""
    root = session_dir(SETTINGS.data_dir, session_id)
    if not root.exists():
        return
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
    if total <= ATTACHMENT_SESSION_QUOTA_BYTES:
        return
    victims: list[Path] = []
    for _mtime, size, p in sorted(entries):  # 最旧在前
        if total <= ATTACHMENT_SESSION_QUOTA_BYTES:
            break
        total -= size
        victims.append(p)
    file_ids = {p.name for p in victims}
    for p in victims:
        with contextlib.suppress(OSError):
            p.unlink()
    rewritten = await _rewrite_rows_referencing(db, session_id, file_ids)
    if rewritten:
        await db.commit()
    logger.info(
        "session video quota eviction",
        extra={
            "session_id": session_id,
            "evicted": len(file_ids),
            "rewritten_rows": rewritten,
            "incoming_bytes": incoming_bytes,
        },
    )


async def prune_videos_in_range(db: AsyncSession, conversation_id: int, *, lo: int = 0, hi: int | None = None) -> None:
    """清理 ``[lo, hi)`` 区间用户行引用的视频文件并改写 part。

    调用时机：压缩/夜间摘要检查点落库后（``hi``=检查点 id，区间行已被摘要覆盖）与
    历史截断删除前（``lo``=截断起点）。这些行不会再进上下文读路径，视频是死重量；
    改写占位而非只删文件，保证水合渲染与 LLM 上下文都不残留死链 URL。
    """
    conditions = [
        Message.conversation_id == conversation_id,
        Message.id >= lo,
        Message.role == "user",
        Message.content_type == "multimodal_v1",
        Message.content.like('%"input_video"%'),
    ]
    if hi is not None:
        conditions.append(Message.id < hi)
    rows = (await db.execute(select(Message.id, Message.content).where(*conditions))).all()
    if not rows:
        return
    file_ids: set[str] = set()
    for _message_id, content in rows:
        parts = safe_json_loads(content, default=[])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "input_video":
                file_id = _file_id_from_url(str(part.get("video_url") or ""), str(conversation_id))
                if file_id is not None:
                    file_ids.add(file_id)
    if not file_ids:
        return
    root = session_dir(SETTINGS.data_dir, str(conversation_id)).resolve()
    removed = 0
    for file_id in file_ids:
        target = (root / file_id).resolve()
        if target.is_relative_to(root):
            with contextlib.suppress(OSError):
                target.unlink()
                removed += 1
    rewritten = await _rewrite_rows_referencing(db, str(conversation_id), file_ids, lo=lo, hi=hi)
    logger.info(
        "session video prune",
        extra={
            "conversation_id": conversation_id,
            "lo": lo,
            "hi": hi,
            "removed_files": removed,
            "rewritten_rows": rewritten,
        },
    )


async def inline_video_parts(items: list, *, expected_session_id: str | None = None) -> list:
    """构造供应商请求前的最后一步：把最近的相对 URL ``input_video`` 内联为 data URL。

    从新到旧分配 ``VIDEO_INLINE_MAX_PER_REQUEST`` 个内联名额；超出、文件缺失或 URL 指向
    非 ``expected_session_id`` 会话的降级为 ``[video]`` 文本占位（与旧图 [screenshot] 同构）。
    公网绝对 URL 原样直通（供应商自行拉取）。仅修改 dict 项的 list content，其他 item 形状原样保留。
    """
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
                    if expected_session_id is None or _file_id_from_url(url, expected_session_id) is not None
                    else {"type": "input_text", "text": VIDEO_DEGRADED_TEXT},
                )
                continue
            inlined = None
            if budget > 0 and expected_session_id:
                file_id = _file_id_from_url(url, expected_session_id)
                path = _video_file_path(expected_session_id, file_id) if file_id else None
                if path is not None and path.is_file():
                    try:
                        raw = await asyncio.to_thread(path.read_bytes)
                        mime = video_mime_for_ext(path.suffix)
                        inlined = {
                            "type": "input_video",
                            "video_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
                        }
                        budget -= 1
                    except OSError:
                        inlined = None
            new_parts.append(inlined if inlined is not None else {"type": "input_text", "text": VIDEO_DEGRADED_TEXT})
        out_items.append({**item, "content": new_parts})
    out_items.reverse()
    return out_items
