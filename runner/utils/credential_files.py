import atexit
import logging
import os
import shutil
import tempfile
from contextvars import ContextVar
from pathlib import Path

from .config import cfg_get, load_config
from .constants import get_spiritagent_dir, get_spiritagent_home
from .file_safety import validate_within_dir

logger = logging.getLogger(__name__)


def get_external_skills_dirs() -> list[Path]:
    try:
        return (
            [Path(p) for p in raw if isinstance(p, str | Path)]
            if isinstance(raw := cfg_get(load_config(), "skills", "external_dirs", default=[]), list)
            else []
        )
    except Exception:
        return []


_registered_files_var: ContextVar[dict[str, str]] = ContextVar("_registered_files")


def _get_registered() -> dict[str, str]:
    if (val := _registered_files_var.get(None)) is None:
        _registered_files_var.set(val := {})
    return val


_config_files: list[dict[str, str]] | None = None


def register_credential_file(relative_path: str, container_base: str = "/root/.spiritagent") -> bool:
    spiritagent_home = get_spiritagent_home()
    if os.path.isabs(relative_path):
        logger.warning(
            "credential_files: rejected absolute path %r (must be relative to SPIRITAGENT_HOME)",
            relative_path,
        )
        return False
    if containment_error := validate_within_dir(host_path := spiritagent_home / relative_path, spiritagent_home):
        logger.warning("credential_files: rejected path traversal %r (%s)", relative_path, containment_error)
        return False
    if not (resolved := host_path.resolve()).is_file():
        logger.debug("credential_files: skipping %s (not found)", resolved)
        return False
    _get_registered()[container_path := f"{container_base.rstrip('/')}/{relative_path}"] = str(resolved)
    logger.debug("credential_files: registered %s -> %s", resolved, container_path)
    return True


def _load_config_files() -> list[dict[str, str]]:
    global _config_files
    if _config_files is not None:
        return _config_files
    _config_files = []
    try:
        spiritagent_home = get_spiritagent_home()
        if isinstance(cred_files := cfg_get(load_config(), "terminal", "credential_files"), list):
            for item in cred_files:
                if not isinstance(item, str) or not (rel := item.strip()):
                    continue
                if os.path.isabs(rel):
                    logger.warning("credential_files: rejected absolute config path %r", rel)
                elif containment_error := validate_within_dir(host_path := spiritagent_home / rel, spiritagent_home):
                    logger.warning("credential_files: rejected config path traversal %r (%s)", rel, containment_error)
                elif (resolved_path := host_path.resolve()).is_file():
                    _config_files.append(
                        {"host_path": str(resolved_path), "container_path": f"/root/.spiritagent/{rel}"},
                    )
    except Exception as e:
        logger.warning("Could not read terminal.credential_files from config: %s", e)
    return _config_files


def reset_cache() -> None:
    """清空由配置派生的挂载列表（spiritagent.config.update 时调用）。"""
    global _config_files
    _config_files = None


def get_credential_file_mounts() -> list[dict[str, str]]:
    mounts = {cp: hp for cp, hp in _get_registered().items() if Path(hp).is_file()}
    cfg_mounts = {
        entry["container_path"]: entry["host_path"]
        for entry in _load_config_files()
        if Path(entry["host_path"]).is_file()
    }
    return [{"host_path": hp, "container_path": cp} for cp, hp in (cfg_mounts | mounts).items()]


def get_skills_directory_mount(container_base: str = "/root/.spiritagent") -> list[dict[str, str]]:
    spiritagent_home = get_spiritagent_home()
    base = container_base.rstrip("/")
    mounts = (
        [{"host_path": _safe_skills_path(skills_dir), "container_path": f"{base}/skills"}]
        if (skills_dir := spiritagent_home / "skills").is_dir()
        else []
    )
    mounts.extend(
        [
            {"host_path": _safe_skills_path(ext_dir), "container_path": f"{base}/external_skills/{idx}"}
            for idx, ext_dir in enumerate(get_external_skills_dirs())
            if ext_dir.is_dir()
        ],
    )
    return mounts


_safe_skills_tempdir: Path | None = None


def _safe_skills_path(skills_dir: Path) -> str:
    global _safe_skills_tempdir
    if not (symlinks := [p for p in skills_dir.rglob("*") if p.is_symlink()]):
        return str(skills_dir)
    for link in symlinks:
        logger.warning("credential_files: skipping symlink in skills dir: %s -> %s", link, os.readlink(link))
    if _safe_skills_tempdir and _safe_skills_tempdir.is_dir():
        shutil.rmtree(_safe_skills_tempdir, ignore_errors=True)
    _safe_skills_tempdir = safe_dir = Path(tempfile.mkdtemp(prefix="spiritagent-skills-safe-"))
    for item in skills_dir.rglob("*"):
        if not item.is_symlink():
            target = safe_dir / item.relative_to(skills_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.is_dir():
                target.mkdir(exist_ok=True)
            elif item.is_file():
                shutil.copy2(str(item), str(target))
    atexit.register(lambda: shutil.rmtree(safe_dir, ignore_errors=True) if safe_dir.is_dir() else None)
    logger.info("credential_files: created symlink-safe skills copy at %s", safe_dir)
    return str(safe_dir)


def iter_skills_files(container_base: str = "/root/.spiritagent") -> list[dict[str, str]]:
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
        # ``follow_symlinks=False`` 防止 rglob 钻进 symlinked 子目录; 否则 ``item.relative_to(s_dir)``
        # 会抛 ValueError 把整个 mount 流程拖垮, 且会无意识地泄露 skills 之外的文件。
        for item in s_dir.rglob("*", follow_symlinks=False):
            if item.is_symlink() or item.is_dir():
                continue
            if not item.is_file():
                continue
            try:
                rel = item.relative_to(s_dir)
            except ValueError:
                continue
            out.append({"host_path": str(item), "container_path": f"{c_root}/{rel}"})
    return out


_CACHE_DIRS: list[tuple[str, str]] = [
    ("cache/documents", "document_cache"),
    ("cache/images", "image_cache"),
    ("cache/audio", "audio_cache"),
    ("cache/screenshots", "browser_screenshots"),
]


def get_cache_directory_mounts(container_base: str = "/root/.spiritagent") -> list[dict[str, str]]:
    return [
        {"host_path": str(host_dir), "container_path": f"{container_base.rstrip('/')}/{new_subpath}"}
        for new_subpath, old_name in _CACHE_DIRS
        if (host_dir := get_spiritagent_dir(new_subpath, old_name)).is_dir()
    ]


def iter_cache_files(container_base: str = "/root/.spiritagent") -> list[dict[str, str]]:
    base = container_base.rstrip("/")
    return [
        {"host_path": str(item), "container_path": f"{base}/{new_subpath}/{item.relative_to(host_dir)}"}
        for new_subpath, old_name in _CACHE_DIRS
        if (host_dir := get_spiritagent_dir(new_subpath, old_name)).is_dir()
        for item in host_dir.rglob("*")
        if not item.is_symlink() and item.is_file()
    ]
