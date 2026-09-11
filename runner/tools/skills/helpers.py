import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from utils import get_disabled_config_names

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """把 content 拆为 (frontmatter, body)。

    没有 frontmatter 块时返回空 dict 与原始 body。frontmatter 按 YAML 解析；
    YAML 解析错误会被吞掉（返回空 dict），以让 skill_view 对畸形 manifest 保持韧性。
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}, content[match.end() :]
    return data if isinstance(data, dict) else {}, content[match.end() :]


def iter_skill_index_files(directory: Path | str, name: str = "SKILL.md") -> Iterator[Path]:
    """产出 directory 下（递归）所有 name 文件。"""
    root = Path(directory)
    if not root.is_dir():
        return
    yield from sorted(root.rglob(name))


def get_spiritagent_metadata(frontmatter: dict[str, Any] | None) -> dict[str, Any]:
    """返回 frontmatter.metadata.spiritagent 作为 dict；任一环节缺失或类型不对则返回 {}。"""
    if not isinstance(frontmatter, dict):
        return {}
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    spiritagent = metadata.get("spiritagent")
    return spiritagent if isinstance(spiritagent, dict) else {}


def get_disabled_skill_names(section: str = "skills") -> set[str]:
    """从内存配置中读取 <section>.disabled 列表。section 默认 "skills"；传 "toolsets" 可让兄弟 toolsets section 复用同一解析路径。"""
    return get_disabled_config_names(section)


def is_excluded_skill_path(path: Path) -> bool:
    return any(p.startswith(".") or p in {"__pycache__", "venv", ".venv", "node_modules"} for p in path.parts)
