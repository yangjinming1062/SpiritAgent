import json
import os
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from components import SETTINGS, owned_temp_files

URL_PREFIXES: dict[str, str] = {
    "/api/companion/asset/": "companion-assets/",
    "/api/media/videos/": "desktop-attachments/",
}

# 用户维度、整目录按 user_id 重命名的资产根目录；备份按该常量在父子两代实例间搬迁。
USER_ASSET_ROOT = "companion-assets"
# 遍历正文中嵌套 JSON 的深度上限，远低于解释器递归限制；更深处不再收集文件引用，也不改写。
_MAX_JSON_DEPTH = 64


def _storage_path(value: str) -> str:
    if not value.startswith(("/", "http://", "https://", "companion-", "desktop-attachments/", "temp-media/")):
        return value
    try:
        path = unquote(urlsplit(value).path)
    except ValueError:
        return value
    for prefix, storage in URL_PREFIXES.items():
        if path.startswith(prefix):
            return storage + path.removeprefix(prefix)
    return path


def _strings(value: Any, depth: int = 0) -> Iterator[str]:
    if depth > _MAX_JSON_DEPTH:
        return
    if isinstance(value, str):
        yield value
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


class UrlRewriter:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping
        self.created: list[Path] = []

    def __call__(self, original: str | None) -> str | None:
        if not original:
            return original
        path = _storage_path(original)
        target = self._mapping.get(path)
        if target is None:
            return original
        for prefix, storage in URL_PREFIXES.items():
            if urlsplit(original).path.startswith(prefix):
                return prefix + target.removeprefix(storage)
        return target

    def rewrite(self, value: Any, depth: int = 0) -> Any:
        if depth > _MAX_JSON_DEPTH:
            return value
        if isinstance(value, dict):
            return {key: self.rewrite(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [self.rewrite(item, depth + 1) for item in value]
        if isinstance(value, str):
            if value.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(value)
                except (ValueError, RecursionError):
                    return self(value)
                rewritten = self.rewrite(parsed, depth + 1)
                return json.dumps(rewritten, ensure_ascii=False) if rewritten != parsed else value
            return self(value)
        return value

    def rollback(self) -> None:
        for path in reversed(self.created):
            path.unlink(missing_ok=True)


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
    return sorted(path for path in files if path.resolve().is_relative_to(root))


def _user_asset_target(relative: PurePosixPath, source_uid: int, target_uid: int) -> PurePosixPath | None:
    """备份中源账户资产在目标账户下的相对路径；不在源账户资产目录内返回 None。"""
    parts = relative.parts
    if parts[0] == USER_ASSET_ROOT and len(parts) >= 3 and parts[1] == str(source_uid):
        return PurePosixPath(parts[0], str(target_uid), *parts[2:])
    return None


def planned_asset_mapping(extract_root: Path, source_uid: int, target_uid: int) -> dict[str, str]:
    """包内账户资产恢复后的路径映射，与 restore_files 同一规则但不复制文件，供预检与写入判断一致。"""
    source_root = extract_root / "files"
    mapping: dict[str, str] = {}
    for source in source_root.rglob("*"):
        if not source.is_file():
            continue
        relative = PurePosixPath(source.relative_to(source_root).as_posix())
        if (target := _user_asset_target(relative, source_uid, target_uid)) is not None:
            mapping[str(relative)] = target.as_posix()
    return mapping


def _ensure_running(stop: threading.Event) -> None:
    if stop.is_set():
        raise InterruptedError("Backup restore was cancelled")


def restore_files(
    extract_root: Path,
    source_uid: int,
    target_uid: int,
    rewriter: UrlRewriter,
    stop: threading.Event,
    *,
    conversations: dict[str, int | str],
    include_conversation_files: bool = True,
) -> int:
    """复制备份文件，映射与新建文件记入调用方持有的 rewriter，返回因会话缺失而跳过的附件数；stop 置位后中止。"""
    root = Path(SETTINGS.data_dir).resolve()
    source_root = extract_root / "files"
    skipped_conversation_files = 0
    temp_ids: dict[str, str] = {}
    try:
        for source in sorted(source_root.rglob("*")):
            _ensure_running(stop)
            if not source.is_file():
                continue
            relative = PurePosixPath(source.relative_to(source_root).as_posix())
            parts = relative.parts
            user_asset = _user_asset_target(relative, source_uid, target_uid)
            if user_asset is not None:
                target_relative = user_asset
            elif parts[0] == "desktop-attachments" and len(parts) >= 3:
                if not include_conversation_files:
                    continue
                if parts[1] not in conversations:
                    skipped_conversation_files += 1
                    continue
                target_relative = PurePosixPath(parts[0], str(conversations[parts[1]]), *parts[2:])
            elif parts[0] == "temp-media" and len(parts) == 2:
                token = temp_ids.setdefault(relative.stem, uuid4().hex)
                target_relative = PurePosixPath("temp-media", token + relative.suffix)
            else:
                raise ValueError(f"Unexpected backup file: {relative}")
            target = (root / str(target_relative)).resolve()
            if not target.is_relative_to(root):
                raise ValueError("Backup destination escapes data directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not target.is_file():
                    raise ValueError("Backup destination exists and is not a file")
            else:
                staged = target.with_name(f".{target.name}.restore_{uuid4().hex}")
                try:
                    shutil.copy2(source, staged)
                    try:
                        os.link(staged, target)
                    except FileExistsError:
                        if not target.is_file():
                            raise ValueError("Backup destination exists and is not a file")
                    else:
                        rewriter.created.append(target)
                finally:
                    staged.unlink(missing_ok=True)
            rewriter._mapping[str(relative)] = target_relative.as_posix()
        for old, new in temp_ids.items():
            _ensure_running(stop)
            rewriter._mapping[f"/api/media/files/{old}"] = f"/api/media/files/{new}"
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
        return skipped_conversation_files
    except Exception:
        # 线程自行回滚：调用方等待线程退出的过程被再次取消时，也不会留下已复制的文件。
        rewriter.rollback()
        raise
