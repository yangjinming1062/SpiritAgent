import logging
import os
from pathlib import Path

from .config import cfg_get, load_config
from .constants import get_spiritagent_dir, get_spiritagent_home
from .file_safety import validate_within_dir

logger = logging.getLogger(__name__)


def get_external_skills_dirs() -> list[Path]:
    if not isinstance(raw := cfg_get(load_config(), "skills", "external_dirs", default=[]), list):
        return []
    return [Path(p) for p in raw if isinstance(p, str)]


_config_files: list[tuple[str, str]] | None = None


def _load_config_files() -> list[tuple[str, str]]:
    """``terminal.credential_files`` 中相对 SPIRITAGENT_HOME 的文件：(宿主绝对路径, 相对路径)。"""
    global _config_files
    if _config_files is not None:
        return _config_files
    _config_files = []
    spiritagent_home = get_spiritagent_home()
    raw = cfg_get(load_config(), "terminal", "credential_files")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, str) or not (rel := item.strip()):
            continue
        if os.path.isabs(rel):
            logger.warning("credential_files: rejected absolute config path %r", rel)
        elif containment_error := validate_within_dir(host_path := spiritagent_home / rel, spiritagent_home):
            logger.warning("credential_files: rejected config path traversal %r (%s)", rel, containment_error)
        elif (resolved_path := host_path.resolve()).is_file():
            _config_files.append((str(resolved_path), Path(rel).as_posix()))
    return _config_files


def reset_cache() -> None:
    """清空由配置派生的凭据文件列表（spiritagent.config.update 时调用）。"""
    global _config_files
    _config_files = None


def get_credential_file_mounts(container_base: str) -> list[dict[str, str]]:
    base = container_base.rstrip("/")
    return [
        {"host_path": host_path, "container_path": f"{base}/{rel}"}
        for host_path, rel in _load_config_files()
        if Path(host_path).is_file()
    ]


def iter_skills_files(container_base: str) -> list[dict[str, str]]:
    spiritagent_home = get_spiritagent_home()
    base = container_base.rstrip("/")
    dirs = [(spiritagent_home / "skills", f"{base}/skills")] if (spiritagent_home / "skills").is_dir() else []
    dirs.extend(
        (ext_dir, f"{base}/external_skills/{idx}")
        for idx, ext_dir in enumerate(get_external_skills_dirs())
        if ext_dir.is_dir()
    )
    out: list[dict[str, str]] = []
    for s_dir, c_root in dirs:
        # rglob 默认不进入链接目录，并跳过链接文件，避免把技能目录之外的文件同步出去。
        for item in s_dir.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            out.append({"host_path": str(item), "container_path": f"{c_root}/{item.relative_to(s_dir).as_posix()}"})
    return out


_CACHE_DIRS: list[str] = [
    "cache/documents",
    "cache/images",
    "cache/audio",
    "cache/screenshots",
]


def iter_cache_files(container_base: str) -> list[dict[str, str]]:
    base = container_base.rstrip("/")
    return [
        {"host_path": str(item), "container_path": f"{base}/{subpath}/{item.relative_to(host_dir).as_posix()}"}
        for subpath in _CACHE_DIRS
        if (host_dir := get_spiritagent_dir(subpath)).is_dir()
        for item in host_dir.rglob("*")
        if not item.is_symlink() and item.is_file()
    ]
