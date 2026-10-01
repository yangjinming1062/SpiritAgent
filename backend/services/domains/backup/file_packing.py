import json
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
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


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except ValueError:
                return
            yield from _strings(parsed)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


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

    def rewrite(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.rewrite(item) for item in value]
        if isinstance(value, str):
            if value.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(value)
                except ValueError:
                    return self(value)
                rewritten = self.rewrite(parsed)
                return json.dumps(rewritten, ensure_ascii=False) if rewritten != parsed else value
            return self(value)
        return value

    def rollback(self) -> None:
        for path in reversed(self.created):
            path.unlink(missing_ok=True)


@dataclass(frozen=True)
class FileRestoreResult:
    rewriter: UrlRewriter
    skipped_conversation_files: int


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


def restore_files(
    extract_root: Path,
    source_uid: int,
    target_uid: int,
    *,
    conversations: dict[str, int | str],
) -> FileRestoreResult:
    root = Path(SETTINGS.data_dir).resolve()
    source_root = extract_root / "files"
    rewriter = UrlRewriter({})
    skipped_conversation_files = 0
    temp_ids: dict[str, str] = {}
    try:
        for source in sorted(source_root.rglob("*")):
            if not source.is_file():
                continue
            relative = PurePosixPath(source.relative_to(source_root).as_posix())
            parts = relative.parts
            if parts[0] == USER_ASSET_ROOT and len(parts) >= 3 and parts[1] == str(source_uid):
                target_relative = PurePosixPath(parts[0], str(target_uid), *parts[2:])
            elif parts[0] == "desktop-attachments" and len(parts) >= 3:
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
        return FileRestoreResult(
            rewriter=rewriter,
            skipped_conversation_files=skipped_conversation_files,
        )
    except Exception:
        rewriter.rollback()
        raise
