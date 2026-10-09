import json
import math
import os
import re
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from components import SETTINGS, owned_temp_files

from services.infrastructure.assets import parse_companion_asset_path

URL_PREFIXES: dict[str, str] = {
    "/api/companion/asset/": "companion-assets/",
    "/api/media/videos/": "desktop-attachments/",
}

# 用户维度、整目录按 user_id 重命名的资产根目录；备份按该常量在父子两代实例间搬迁。
USER_ASSET_ROOT = "companion-assets"
# 遍历正文中嵌套 JSON 的深度上限；导出不继续收集，恢复按类拒绝无法完整校验的资料。
_MAX_JSON_DEPTH = 64
_TEMP_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_TEMP_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff"})
_ASSET_TOKEN = re.compile(
    r"(?:https?://[^\s<>\"'()]+)?/api/companion/asset/\d+/[A-Za-z0-9._/-]+(?:\?[^\s<>\"'()]*)?|companion-assets/\d+/[A-Za-z0-9._/-]+",
)


def _storage_path(value: str) -> str:
    if not value.startswith(("/", "http://", "https://", "companion-", "desktop-attachments/", "temp-media/")):
        return value
    try:
        path = unquote(urlsplit(value).path)
    except ValueError:
        return value
    for prefix, storage in URL_PREFIXES.items():
        if (start := path.find(prefix)) >= 0:
            return storage + path[start + len(prefix) :]
    if (start := path.find("/api/media/files/")) >= 0:
        return path[start:]
    return path


def _strings(value: Any, depth: int = 0) -> Iterator[str]:
    if depth > _MAX_JSON_DEPTH:
        return
    if isinstance(value, str):
        yield value
        yield from (match.group().rstrip(".!?，。；！") for match in _ASSET_TOKEN.finditer(value))
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                return
            yield from _strings(parsed, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, depth + 1)


def _temporary_payload(source_root: Path, file_id: str, source_user_id: int) -> Path | None:
    """临时图片必须有同账户元数据；已清理的正文作为失效媒体处理。"""
    metadata = source_root / "temp-media" / f"{file_id}.json"
    if not metadata.is_file():
        return None
    try:
        meta = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raise ValueError("临时媒体元数据无法读取。") from None
    if not isinstance(meta, dict) or type(meta.get("user_id")) is not int or meta["user_id"] != source_user_id:
        raise ValueError("临时媒体不属于备份账户。")
    if (
        not isinstance(meta.get("path"), str)
        or not meta["path"]
        or "\x00" in meta["path"]
        or not isinstance(meta.get("content_type"), str)
        or meta["content_type"] not in _TEMP_IMAGE_TYPES
        or type(meta.get("size")) is not int
        or meta["size"] < 0
        or type(meta.get("created_at")) not in {int, float}
        or isinstance(meta["created_at"], float)
        and not math.isfinite(meta["created_at"])
        or meta["created_at"] < 0
    ):
        raise ValueError("临时媒体元数据无效。")
    name = Path(meta["path"]).name
    payload = metadata.with_name(name)
    if payload.stem != file_id or payload == metadata or "/" in name or "\\" in name:
        raise ValueError("临时媒体正文与元数据不匹配。")
    if not payload.is_file():
        return None
    if payload.stat().st_size != meta["size"]:
        raise ValueError("临时媒体大小与元数据不匹配。")
    return payload


class UrlRewriter:
    def __init__(
        self,
        mapping: dict[str, str],
        *,
        source_user_id: int | None = None,
        target_user_id: int | None = None,
        extract_root: Path | None = None,
        conversation_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._mapping = mapping
        self.created: list[Path] = []
        self._source_user_id = source_user_id
        self._target_user_id = target_user_id
        self._source_root = (extract_root / "files").resolve() if extract_root is not None else None
        self._target_root = Path(SETTINGS.data_dir).resolve()
        self._conversation_ids = conversation_ids
        self.conversations: dict[str, int | str] = {}
        self.temp_ids: dict[str, str] = {}
        self._temp_payloads: dict[str, Path | None] = {}
        self._available_files: dict[str, Path | None] = {}
        self.missing_paths: set[str] = set()
        self.inserted_rows: set[tuple[str, str]] = set()
        self.asset_copies: dict[str, str] = {}

    def set_asset_mapping(self, mapping: dict[str, str], copies: dict[str, str]) -> None:
        self._mapping.update(mapping)
        self.asset_copies = copies
        self._available_files.clear()
        self.missing_paths.clear()

    def _temporary_source(self, file_id: str) -> Path | None:
        if not _TEMP_ID_PATTERN.fullmatch(file_id):
            raise ValueError("临时媒体标识无效。")
        if file_id not in self._temp_payloads:
            if self._source_root is None or self._source_user_id is None:
                raise ValueError("缺少临时媒体归属资料。")
            self._temp_payloads[file_id] = _temporary_payload(self._source_root, file_id, self._source_user_id)
        return self._temp_payloads[file_id]

    def _owned_target(self, path: str) -> str | None:
        if self._source_user_id is None or self._target_user_id is None or self._source_root is None:
            return None
        if path.startswith("companion-assets/"):
            parsed = parse_companion_asset_path(path)
            if parsed is None or parsed[0] != self._source_user_id:
                raise ValueError("媒体素材路径不属于备份账户。")
            return f"companion-assets/{self._target_user_id}/{parsed[1]}"
        if path.startswith("desktop-attachments/"):
            parts = path.split("/")
            if (
                len(parts) != 3
                or not parts[1].isdigit()
                or not parts[2]
                or parts[2] in {".", ".."}
                or "\\" in path
                or "\x00" in path
            ):
                raise ValueError("会话附件路径无效。")
            if self._conversation_ids and parts[1] not in self._conversation_ids:
                raise ValueError("附件不属于备份中的会话。")
            target_id = self.conversations.get(parts[1])
            return f"desktop-attachments/{target_id}/{parts[2]}" if target_id is not None else ""
        if path.startswith(("temp-media/", "/api/media/files/")):
            name = path.removeprefix("temp-media/").removeprefix("/api/media/files/")
            file_id = PurePosixPath(name).stem if path.startswith("temp-media/") else name
            if "/" in name or "\\" in name or "\x00" in name:
                raise ValueError("临时媒体路径无效。")
            payload = self._temporary_source(file_id)
            if path.startswith("temp-media/") and PurePosixPath(name).suffix:
                if PurePosixPath(name).suffix.lower() not in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                    ".gif",
                    ".bmp",
                    ".tif",
                    ".tiff",
                }:
                    raise ValueError("临时媒体正文扩展名无效。")
                if payload is not None and payload.suffix != PurePosixPath(name).suffix:
                    raise ValueError("临时媒体正文与引用不匹配。")
            token = self.temp_ids.setdefault(file_id, uuid4().hex)
            return (
                f"temp-media/{token}{PurePosixPath(name).suffix}"
                if path.startswith("temp-media/")
                else f"/api/media/files/{token}"
            )
        return None

    def _available_file(self, path: str, target: str) -> Path | None:
        if path not in self._available_files:
            candidate: Path | None = None
            if self._source_root is not None:
                if path.startswith(("temp-media/", "/api/media/files/")):
                    file_id = PurePosixPath(path).stem
                    candidate = self._temporary_source(file_id)
                else:
                    destination = self._target_root / target
                    if target and destination.resolve() != destination:
                        raise ValueError("目标素材路径经符号链接指向其他位置，无法安全恢复。")
                    candidate = destination if target and destination.is_file() else self._source_root / path
            self._available_files[path] = candidate if candidate is not None and candidate.is_file() else None
        return self._available_files[path]

    def require_file(self, original: str) -> None:
        path = _storage_path(original)
        target = self._mapping.get(path, self._owned_target(path))
        if target is None or not target or self._available_file(path, target) is None:
            raise ValueError("必需的身份、场景或已发布媒体不在备份与目标账户中，无法恢复此类别。")

    def __call__(self, original: str | None) -> str | None:
        if not original:
            return original
        path = _storage_path(original)
        owned_target = self._owned_target(path)
        target = self._mapping.get(path, owned_target)
        if target is None:
            return original
        if owned_target is not None and (not target or self._available_file(path, target) is None):
            self.missing_paths.add(path)
        if not target:
            return ""
        for prefix, storage in URL_PREFIXES.items():
            if prefix in urlsplit(original).path:
                return prefix + target.removeprefix(storage)
        return target

    def rewrite(self, value: Any, depth: int = 0) -> Any:
        return self._walk(value, depth, apply=True)

    def scan(self, value: Any, depth: int = 0) -> None:
        """与 rewrite 同一遍历只收集副作用，不重建容器、不回写 JSON 字符串。"""
        self._walk(value, depth, apply=False)

    # 单一遍历骨架：rewrite（apply=True）重建容器，scan（apply=False）只收集副作用。
    def _walk(self, value: Any, depth: int, *, apply: bool) -> Any:
        if depth > _MAX_JSON_DEPTH:
            raise ValueError("备份资料嵌套过深，无法安全校验媒体归属。")
        if isinstance(value, dict):
            result: dict[Any, Any] = {}
            for key, item in value.items():  # 只扫值，不扫键
                walked = self._walk(item, depth + 1, apply=apply)
                if apply:
                    result[key] = walked
            return result if apply else None
        if isinstance(value, list):
            items: list[Any] = []
            for item in value:
                walked = self._walk(item, depth + 1, apply=apply)
                if apply:
                    items.append(walked)
            return items if apply else None
        if isinstance(value, str):
            if value.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(value)
                except RecursionError:
                    raise ValueError("备份资料嵌套过深，无法安全校验媒体归属。") from None
                except ValueError:
                    pass
                else:
                    # JSON 串只递归解析结果，不再落 token/整串分支。
                    rewritten = self._walk(parsed, depth + 1, apply=apply)
                    if not apply:
                        return None
                    return json.dumps(rewritten, ensure_ascii=False) if rewritten != parsed else value
            if _ASSET_TOKEN.search(value):

                def rewrite_token(match: re.Match[str]) -> str:
                    token = match.group()
                    reference = token.rstrip(".!?，。；！")
                    return (self(reference) or "") + token[len(reference) :]

                # sub 逐命中调用 rewrite_token，副作用顺序与 scan 模式一致，仅丢弃替换结果。
                rewritten = _ASSET_TOKEN.sub(rewrite_token, value)
                return rewritten if apply else None
            rewritten = self(value)
            return rewritten if apply else None
        return value if apply else None

    def rollback(self) -> None:
        for path in reversed(self.created):
            path.unlink(missing_ok=True)


def validate_row_files(table: str, rows: list[dict[str, Any]], rewriter: UrlRewriter) -> None:
    """预检工作线程核对必需文件；旧历史可保留失效引用，不把身份或发布资产当作已恢复。"""
    rewriter.scan(rows)
    for row in rows:
        required: list[Any] = []
        if table == "avatar_assets":
            required.append(row.get("asset_url"))
            if row.get("seed_fullbody_url") or row.get("is_fullbody_confirmed"):
                required.append(row.get("seed_fullbody_url"))
        elif table == "companion_outfits" and row.get("status") == "ready":
            required.append(row.get("fullbody_url"))
        elif table == "companion_scenes":
            if row.get("status") == "ready":
                required.append(row.get("media_path"))
            if row.get("upload_source_path"):
                required.append(row["upload_source_path"])
        elif table == "desktop_video_sets":
            context = json.loads(row["context_json"])
            required.extend((context.get("identity_path"), context.get("outfit_path")))
        elif table == "desktop_video_actions":
            if row.get("accepted_asset_json"):
                asset = json.loads(row["accepted_asset_json"])
                required.extend((asset.get("video_path"), asset.get("poster_path")))
            if row.get("status") == "review_pending" and row.get("generation_state_json"):
                candidate = json.loads(row["generation_state_json"]).get("candidate")
                if candidate:
                    required.extend((candidate.get("video_path"), candidate.get("poster_path")))
        elif table == "companion_posts":
            if row.get("content_type") != "text":
                required.append(row.get("media_url"))
            if row.get("audio_url"):
                required.append(row["audio_url"])
        for reference in required:
            if not isinstance(reference, str) or not reference:
                raise ValueError("必需的身份、场景或已发布媒体缺少引用，无法恢复此类别。")
            rewriter.require_file(reference)


def collect_files_for_export(user_id: int, rows: dict[str, list[dict[str, Any]]]) -> list[Path]:
    root = Path(SETTINGS.data_dir).resolve()
    files: set[Path] = set()
    files.update(path for path in (root / USER_ASSET_ROOT / str(user_id)).rglob("*") if path.is_file())
    session_ids = {str(row["id"]) for row in rows.get("conversations", [])}
    for session_id in session_ids:
        files.update(path for path in (root / "desktop-attachments" / session_id).rglob("*") if path.is_file())
    for value in _strings(rows):
        path = _storage_path(value)
        if path.startswith("/api/media/files/"):
            # 临时媒体目录为所有用户共用，只导出元数据归属本用户的文件。
            files.update(owned_temp_files(path.removeprefix("/api/media/files/"), user_id))
        elif path.startswith("temp-media/"):
            name = path.removeprefix("temp-media/")
            if "/" not in name and "\\" not in name:
                files.update(owned_temp_files(PurePosixPath(name).stem, user_id))
    if any(path.resolve() != path or not path.is_relative_to(root) for path in files):
        raise ValueError("备份资源包含符号链接或越界路径，无法安全导出。")
    return sorted(files)


def _user_asset_target(relative: PurePosixPath, source_uid: int, target_uid: int) -> PurePosixPath | None:
    """备份中源账户资产在目标账户下的相对路径；不在源账户资产目录内返回 None。"""
    parts = relative.parts
    if parts[0] == USER_ASSET_ROOT and len(parts) >= 3 and parts[1] == str(source_uid):
        return PurePosixPath(parts[0], str(target_uid), *parts[2:])
    return None


def planned_asset_mapping(extract_root: Path, source_uid: int, target_uid: int) -> dict[str, str]:
    """包内账户资产恢复后的路径映射，与 restore_files 同一规则但不复制文件，供预检与写入判断一致。"""
    source_root = (extract_root / "files").resolve()
    mapping: dict[str, str] = {}
    for source in source_root.rglob("*"):
        if not source.is_file():
            continue
        relative = PurePosixPath(source.relative_to(source_root).as_posix())
        if (target := _user_asset_target(relative, source_uid, target_uid)) is not None:
            mapping[str(relative)] = target.as_posix()
    return mapping


def referenced_backup_files(extract_root: Path, rows: dict[str, list[dict[str, Any]]]) -> frozenset[str]:
    """仅选通过恢复校验的数据行引用的文件；临时媒体的 payload 与元数据成对选择。"""
    references = {_storage_path(value) for value in _strings(rows)}
    temp_ids = {
        PurePosixPath(path).stem for path in references if path.startswith(("temp-media/", "/api/media/files/"))
    }
    source_root = (extract_root / "files").resolve()
    return frozenset(
        relative.as_posix()
        for source in source_root.rglob("*")
        if source.is_file()
        and (
            (relative := PurePosixPath(source.relative_to(source_root).as_posix())).as_posix() in references
            or relative.parts[0] == "temp-media"
            and relative.stem in temp_ids
        )
    )


def _ensure_running(stop: threading.Event) -> None:
    if stop.is_set():
        raise InterruptedError("Backup restore was cancelled")


def _stage_copy(source: Path, target: Path, rewriter: UrlRewriter, stop: threading.Event) -> None:
    """先写隐藏临时文件再硬链接到目标；失败或取消不留下半成品。"""
    staged = target.with_name(f".{target.name}.restore_{uuid4().hex}")
    try:
        shutil.copy2(source, staged)
        _ensure_running(stop)
        try:
            os.link(staged, target)
        except FileExistsError:
            if not target.is_file():
                raise ValueError("Backup destination exists and is not a file")
        else:
            rewriter.created.append(target)
    finally:
        staged.unlink(missing_ok=True)


def restore_files(
    extract_root: Path,
    source_uid: int,
    target_uid: int,
    rewriter: UrlRewriter,
    stop: threading.Event,
    *,
    conversations: dict[str, int | str],
    referenced_files: frozenset[str],
    include_conversation_files: bool = True,
) -> int:
    """复制备份文件，映射与新建文件记入调用方持有的 rewriter，返回因会话缺失而跳过的附件数；stop 置位后中止。"""
    root = Path(SETTINGS.data_dir).resolve()
    source_root = (extract_root / "files").resolve()
    skipped_conversation_files = 0
    temp_ids: dict[str, str] = {}
    try:
        for source in sorted(source_root.rglob("*")):
            _ensure_running(stop)
            if not source.is_file():
                continue
            relative = PurePosixPath(source.relative_to(source_root).as_posix())
            if relative.as_posix() not in referenced_files:
                continue
            parts = relative.parts
            user_asset = _user_asset_target(relative, source_uid, target_uid)
            if user_asset is not None:
                target_relative = PurePosixPath(rewriter._mapping.get(str(relative), str(user_asset)))
            elif parts[0] == "desktop-attachments" and len(parts) >= 3:
                if not include_conversation_files:
                    continue
                if parts[1] not in conversations:
                    skipped_conversation_files += 1
                    continue
                target_relative = PurePosixPath(parts[0], str(conversations[parts[1]]), *parts[2:])
            elif parts[0] == "temp-media" and len(parts) == 2:
                payload = rewriter._temporary_source(relative.stem)
                if payload is None:
                    continue
                if source.name not in {payload.name, f"{relative.stem}.json"}:
                    raise ValueError("临时媒体正文与元数据不匹配。")
                token = rewriter.temp_ids.setdefault(relative.stem, uuid4().hex)
                temp_ids[relative.stem] = token
                target_relative = PurePosixPath("temp-media", token + relative.suffix)
            else:
                raise ValueError(f"Unexpected backup file: {relative}")
            target = root / str(target_relative)
            if target.resolve() != target or not target.is_relative_to(root):
                raise ValueError("Backup destination escapes data directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not target.is_file():
                    raise ValueError("Backup destination exists and is not a file")
            else:
                _stage_copy(source, target, rewriter, stop)
            rewriter._mapping[str(relative)] = target_relative.as_posix()
        # 共享源素材在不同资产包中各自拥有副本；复制失败仍由 rewriter 统一回滚。
        for target_relative, source_relative in rewriter.asset_copies.items():
            _ensure_running(stop)
            if source_relative not in referenced_files:
                continue
            source = source_root / source_relative
            target = root / target_relative
            if source.resolve() != source:
                raise ValueError("Backup source contains symbolic links")
            if target.resolve() != target or not target.is_relative_to(root):
                raise ValueError("Backup destination escapes data directory")
            if not source.is_file() or target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _stage_copy(source, target, rewriter, stop)
        for old, new in temp_ids.items():
            _ensure_running(stop)
            rewriter._mapping[f"/api/media/files/{old}"] = f"/api/media/files/{new}"
            rewriter._mapping[f"temp-media/{old}"] = f"temp-media/{new}"
            meta_path = root / "temp-media" / f"{new}.json"
            if meta_path not in rewriter.created:
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            old_path = "temp-media/" + Path(meta["path"]).name
            restored_path = rewriter._mapping.get(old_path)
            if restored_path is None:
                raise ValueError("Temporary media payload is missing")
            meta["path"] = str(root / restored_path)
            meta["user_id"] = target_uid
            meta["created_at"] = time.time()
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _ensure_running(stop)
        return skipped_conversation_files
    except Exception:
        # 包括最后一次复制后才收到的停止信号；回滚结束前线程不会交还目标文件的所有权。
        rewriter.rollback()
        raise
