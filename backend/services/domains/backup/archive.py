"""备份 zip 的打包与解压校验。

包内布局（`manifest.json`、`db/{表}.json`、`files/{相对 data_dir 的路径}`）同时是 `manifest.py`、`serializers.py`、`file_packing.py` 与 `action_assets.py` 读取的契约，调整须一并修改。
"""

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from components import SETTINGS
from modules.auth import User

from .manifest import build_manifest

_MAX_EXTRACTED_BYTES = 4 * 1024**3
# 打包时分块复制文件并计算校验和，大文件不整体驻留内存。
_FILE_CHUNK_BYTES = 1024 * 1024


class BackupArchiveError(ValueError):
    """备份 zip 本身无效；消息可直接展示给管理员。"""


class BackupArchiveTooLargeError(BackupArchiveError):
    """备份解压后超过大小上限。"""


def write_backup_archive(
    archive_path: Path,
    user: User,
    exported_by: str,
    rows_by_table: dict[str, list[dict[str, Any]]],
    files: list[Path],
) -> None:
    """写出备份 zip：各表数据行、用户文件，以及带校验和的清单。"""
    data_dir_root = Path(SETTINGS.data_dir).resolve()
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = build_manifest(user, rows_by_table, exported_by)
        checksums: dict[str, str] = {}
        for tbl, rs in rows_by_table.items():
            content = json.dumps({"table": tbl, "rows": rs}, ensure_ascii=False).encode("utf-8")
            name = f"db/{tbl}.json"
            zf.writestr(name, content)
            checksums[name] = hashlib.sha256(content).hexdigest()
        for src in files:
            arc = "files/" + src.relative_to(data_dir_root).as_posix()
            with src.open("rb") as source, zf.open(arc, "w") as target:
                digest = hashlib.sha256()
                while chunk := source.read(_FILE_CHUNK_BYTES):
                    target.write(chunk)
                    digest.update(chunk)
            checksums[arc] = digest.hexdigest()
        manifest["checksums"] = checksums
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))


def extract_backup_archive(zip_path: Path, extract_root: Path) -> None:
    """校验总大小、重复条目与路径越界后解压；清单、库存与校验和由 `load_manifest` 校验。"""
    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile:
        raise BackupArchiveError("无效 zip 文件。") from None

    try:
        if sum(entry.file_size for entry in zf.infolist()) > _MAX_EXTRACTED_BYTES:
            raise BackupArchiveTooLargeError(f"备份解压后超过 {_MAX_EXTRACTED_BYTES // 1024**3} GB。")
        if len(set(zf.namelist())) != len(zf.namelist()):
            raise BackupArchiveError("备份包含重复文件。")
        extract_resolved = extract_root.resolve()
        for name in zf.namelist():
            target_path = (extract_root / name).resolve()
            if not target_path.is_relative_to(extract_resolved):
                raise BackupArchiveError(f"非法归档条目：{name}")
            if name.endswith("/"):
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
    finally:
        zf.close()
