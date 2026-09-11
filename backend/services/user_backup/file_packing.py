import json
import shutil
import time
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from components import SETTINGS

URL_PREFIXES: dict[str, str] = {
    "/api/companion/asset/": "companion-assets/",
    "/api/companion/avatar/file/": "companion-avatars/",
    "/api/companion/model/file/": "companion-models/",
    "/api/media/videos/": "desktop-attachments/",
}
USER_DIRECTORIES = ("companion-assets", "companion-models")


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
        if value.startswith(("{", "[")):
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
            if value.startswith(("{", "[")):
                try:
                    parsed = json.loads(value)
                except ValueError:
                    return self(value)
                rewritten = self.rewrite(parsed)
                return json.dumps(rewritten, ensure_ascii=False) if rewritten != parsed else value
            return self(value)
        return value

    def write_manifest(self, storage_path: str | None, content: str) -> None:
        if storage_path and (target := (Path(SETTINGS.data_dir) / storage_path).resolve()) in self.created:
            target.write_text(content, encoding="utf-8")

    def rollback(self) -> None:
        for path in reversed(self.created):
            path.unlink(missing_ok=True)


def collect_files_for_export(user_id: int, rows: dict[str, list[dict[str, Any]]]) -> list[Path]:
    root = Path(SETTINGS.data_dir).resolve()
    files: set[Path] = set()
    for namespace in USER_DIRECTORIES:
        files.update(path for path in (root / namespace / str(user_id)).rglob("*") if path.is_file())
    session_ids = {str(row["id"]) for row in rows.get("conversations", [])}
    for session_id in session_ids:
        files.update(path for path in (root / "desktop-attachments" / session_id).rglob("*") if path.is_file())
    for value in _strings(rows):
        path = _storage_path(value)
        if path.startswith("/api/media/files/"):
            file_id = path.removeprefix("/api/media/files/")
            if not file_id or "/" in file_id or "\\" in file_id or ".." in file_id:
                continue
            files.update(p for p in (root / "temp-media").glob(f"{file_id}.*") if p.is_file())
        elif path.startswith(("companion-avatars/", "temp-media/")):
            target = (root / path).resolve()
            if target.is_relative_to(root / path.split("/")[0]) and target.is_file():
                files.add(target)
                if path.startswith("temp-media/") and target.with_suffix(".json").is_file():
                    files.add(target.with_suffix(".json"))
    return sorted(path for path in files if path.resolve().is_relative_to(root))


def restore_files(
    extract_root: Path,
    source_uid: int,
    target_uid: int,
    *,
    conversations: dict[str, int | str],
) -> UrlRewriter:
    root = Path(SETTINGS.data_dir).resolve()
    source_root = extract_root / "files"
    rewriter = UrlRewriter({})
    temp_ids: dict[str, str] = {}
    try:
        for source in sorted(source_root.rglob("*")):
            if not source.is_file():
                continue
            relative = PurePosixPath(source.relative_to(source_root).as_posix())
            parts = relative.parts
            if parts[0] in USER_DIRECTORIES and len(parts) >= 3 and parts[1] == str(source_uid):
                target_relative = PurePosixPath(parts[0], str(target_uid), *parts[2:])
            elif parts[0] == "desktop-attachments" and len(parts) >= 3 and parts[1] in conversations:
                target_relative = PurePosixPath(parts[0], str(conversations[parts[1]]), *parts[2:])
            elif parts[0] == "companion-avatars" and len(parts) == 2:
                target_relative = relative
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
                target = target.with_name(f"{target.stem}_imp_{uuid4().hex}{target.suffix}")
            rewriter.created.append(target)
            shutil.copy2(source, target)
            rewriter._mapping[str(relative)] = target.relative_to(root).as_posix()
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
            meta["session_id"] = str(conversations.get(str(meta.get("session_id")), ""))
            meta["created_at"] = time.time()
            if "marker" in meta:
                meta["marker"] = f"preview:{target_uid}"
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return rewriter
    except Exception:
        rewriter.rollback()
        raise
