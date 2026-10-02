import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml
from utils import get_disabled_config_names, has_traversal_component, visible_skill_path, visible_skill_roots

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_EXCLUDED_DIR_NAMES = frozenset({"__pycache__", "venv", ".venv", "node_modules"})


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """拆出 (frontmatter, body)；无块或 YAML 畸形时 frontmatter 为空 dict。"""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except (yaml.YAMLError, ValueError):
        return {}, content[match.end() :]
    return data if isinstance(data, dict) else {}, content[match.end() :]


def iter_skill_files(root: Path) -> Iterator[Path]:
    """按路径序产出可见 SKILL.md；跳过隐藏/依赖目录与越出 root 或跨学习域的路径。"""
    if not root.is_dir():
        return
    for skill_md in sorted(root.rglob("SKILL.md")):
        rel_parts = skill_md.relative_to(root).parts
        if any(p.startswith(".") or p in _EXCLUDED_DIR_NAMES for p in rel_parts):
            continue
        if visible_skill_path(skill_md, root):
            yield skill_md


def skill_lookup_path_error(name: str) -> str | None:
    if not isinstance(name, str):
        return "Skill name must be a string."
    candidate = name.strip()
    if (
        PurePosixPath(candidate).is_absolute()
        or PureWindowsPath(candidate).is_absolute()
        or PureWindowsPath(candidate).drive
    ):
        return "Skill name must be a relative path within the skills directory."
    return "Skill name cannot contain '..' path traversal components." if has_traversal_component(candidate) else None


def find_skill_candidates(name: str) -> tuple[Path | None, list[Path]]:
    """按目录名或相对路径匹配；首个有匹配的根胜出，同根歧义交给调用方拒绝。"""
    if error := skill_lookup_path_error(name):
        raise ValueError(error)
    wanted = Path(name)
    for root in visible_skill_roots():
        matches = [
            skill_md
            for skill_md in iter_skill_files(root)
            if skill_md.parent.name == name or skill_md.parent.relative_to(root) == wanted
        ]
        if matches:
            return root, matches
    return None, []


def get_skill_description(frontmatter: dict[str, Any], body: str = "") -> str:
    description = frontmatter.get("description", "")
    if isinstance(description, str) and description:
        return description
    return next(
        (line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")),
        "",
    )


def get_spiritagent_metadata(frontmatter: dict[str, Any] | None) -> dict[str, Any]:
    """返回 frontmatter.metadata.spiritagent 作为 dict；任一环节缺失或类型不对则返回 {}。"""
    if not isinstance(frontmatter, dict):
        return {}
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    spiritagent = metadata.get("spiritagent")
    return spiritagent if isinstance(spiritagent, dict) else {}


def get_disabled_skill_names() -> set[str]:
    """从内存配置中读取 ``skills.disabled`` 列表。"""
    return get_disabled_config_names("skills")
