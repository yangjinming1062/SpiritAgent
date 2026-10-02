import json
import logging
import re
import shutil
import threading
from pathlib import Path
from typing import Any

import yaml
from utils import (
    atomic_replace,
    has_traversal_component,
    is_interrupted,
    learned_skills_root,
    redact_sensitive_text,
    validate_within_dir,
    visible_skill_path,
)

from ..files import format_no_match_hint, fuzzy_find_and_replace
from ..registry import registry, tool_error
from .helpers import find_skill_candidates

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000
MAX_SKILL_FILE_BYTES = 1_048_576
VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}
_mutation_lock = threading.RLock()
logger = logging.getLogger(__name__)


def _validate_name(name: str) -> str | None:
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME_RE.match(name):
        return f"Invalid skill name '{name}'. Use lowercase letters, numbers, hyphens, dots, and underscores. Must start with a letter or digit."
    return None


def _validate_category(category: str) -> str | None:
    if len(category) > MAX_NAME_LENGTH or not VALID_NAME_RE.match(category):
        return f"Invalid category '{category}'. Use lowercase letters, numbers, hyphens, dots, and underscores. Categories must be a single directory name."
    return None


def _validate_frontmatter(content: str, name: str) -> str | None:
    if not content.strip():
        return "Content cannot be empty."
    if not content.startswith("---"):
        return "SKILL.md must start with YAML frontmatter (---). See existing skills for format."
    if not (end_match := re.search(r"\n---\s*\n", content[3:])):
        return "SKILL.md frontmatter is not closed. Ensure you have a closing '---' line."
    try:
        parsed = yaml.safe_load(content[3 : end_match.start() + 3])
    except (yaml.YAMLError, ValueError) as e:
        return f"YAML frontmatter parse error: {e}"
    if not isinstance(parsed, dict):
        return "Frontmatter must be a YAML mapping (key: value pairs)."
    if "name" not in parsed:
        return "Frontmatter must include 'name' field."
    # 列表按 frontmatter name 展示、查找与管理按目录名进行，两者不一致会让技能列得出却打不开。
    if parsed["name"] != name:
        return f"Frontmatter 'name' must be '{name}' (the skill name)."
    if "description" not in parsed:
        return "Frontmatter must include 'description' field."
    if not isinstance(parsed["description"], str):
        return "Frontmatter 'description' must be a string."
    if len(parsed["description"]) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    if not content[end_match.end() + 3 :].strip():
        return "SKILL.md must have content after the frontmatter (instructions, procedures, etc.)."
    return None


def _validate_content_size(content: str, label: str = "SKILL.md") -> str | None:
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return f"{label} content is {len(content):,} characters (limit: {MAX_SKILL_CONTENT_CHARS:,}). Consider splitting into a smaller SKILL.md with supporting files in references/ or templates/."
    return None


def _validate_file_path(file_path: str) -> str | None:
    """校验技能内的支持文件路径：必须位于允许的子目录下，且不能是 SKILL.md（避免嵌套出新技能）。"""
    if not file_path:
        return "file_path is required."
    if has_traversal_component(file_path):
        return "Path traversal ('..') is not allowed."
    normalized = Path(file_path)
    if not normalized.parts or normalized.parts[0] not in ALLOWED_SUBDIRS:
        return f"File must be under one of: {', '.join(sorted(ALLOWED_SUBDIRS))}. Got: '{file_path}'"
    if len(normalized.parts) < 2:
        return f"Provide a file path, not just a directory. Example: '{normalized.parts[0]}/myfile.md'"
    if normalized.name == "SKILL.md":
        return "SKILL.md cannot be placed in a supporting directory; use action='edit' or 'patch' for the main file."
    return None


def _scoped_skill_dir(rel_dir: Path) -> Path:
    """返回当前学习域内 rel_dir 对应的技能目录；解析后越出本域时拒绝。"""
    root = learned_skills_root()
    target = root / rel_dir
    if not visible_skill_path(target, root):
        raise ValueError("Skill path escapes its scope")
    return target


def _find_skill(name: str) -> tuple[Path, Path] | None:
    """按根目录优先级（当前学习域先于共享技能）查找技能，返回 ``(所在根, 技能目录)``。"""
    root, candidates = find_skill_candidates(name)
    if len(candidates) > 1:
        raise ValueError(f"Ambiguous skill name '{name}': {len(candidates)} skills match; use a categorized path.")
    return (root, candidates[0].parent) if root is not None else None


def _in_scope(skill_dir: Path) -> bool:
    return skill_dir.resolve().is_relative_to(learned_skills_root().resolve())


def _writable_skill_dir(root: Path, skill_dir: Path) -> tuple[Path, bool]:
    """返回可写技能目录；共享技能先复制到本域。调用方校验通过后才调用。"""
    if _in_scope(skill_dir):
        return skill_dir, False
    if any(path.is_symlink() or not visible_skill_path(path, skill_dir) for path in skill_dir.rglob("*")):
        raise ValueError("Cannot copy a shared skill containing symlinks")
    target = _scoped_skill_dir(skill_dir.relative_to(root))
    target.mkdir(parents=True)
    try:
        shutil.copytree(skill_dir, target, dirs_exist_ok=True)
    except BaseException:
        _rollback_new_copy(target)
        raise
    return target, True


def _rollback_new_copy(skill_dir: Path) -> None:
    try:
        shutil.rmtree(skill_dir)
    except Exception as exc:
        logger.warning("Failed to remove newly created skill copy %s: %s", skill_dir, redact_sensitive_text(str(exc)))


def _write_skill_file(skill_dir: Path, fresh_copy: bool, target: Path, content: str) -> None:
    """原子写入 target；失败时删除本次新建的副本，避免它遮蔽共享技能（原文件由原子替换保证完好）。"""
    try:
        atomic_replace(str(target), content)
    except BaseException:
        if fresh_copy:
            _rollback_new_copy(skill_dir)
        raise


def _skill_not_found_error(name: str, suffix: str = "") -> str:
    return f"Skill '{name}' not found. Use skills_list() to see available skills.{suffix}"


def _create_skill(name: str, content: str, category: str | None = None) -> dict[str, Any]:
    category = category.strip() if category else ""
    if err := _validate_name(name):
        return {"success": False, "error": err}
    if category and (err := _validate_category(category)):
        return {"success": False, "error": err}
    if err := _validate_frontmatter(content, name):
        return {"success": False, "error": err}
    if err := _validate_content_size(content):
        return {"success": False, "error": err}
    if _find_skill(name):
        return {"success": False, "error": f"A skill named '{name}' already exists."}
    skill_dir = _scoped_skill_dir(Path(category, name))
    skill_dir.mkdir(parents=True)
    _write_skill_file(skill_dir, True, skill_dir / "SKILL.md", content)
    result = {
        "success": True,
        "message": f"Skill '{name}' created.",
        "path": str(skill_dir.relative_to(learned_skills_root())),
        "skill_md": str(skill_dir / "SKILL.md"),
    }
    if category:
        result["category"] = category
    result["hint"] = (
        f"To add reference files, templates, or scripts, use skill_manage(action='write_file', name='{name}', file_path='references/example.md', file_content='...')"
    )
    return result


def _edit_skill(name: str, content: str) -> dict[str, Any]:
    if err := _validate_frontmatter(content, Path(name).name):
        return {"success": False, "error": err}
    if err := _validate_content_size(content):
        return {"success": False, "error": err}
    if not (found := _find_skill(name)):
        return {"success": False, "error": _skill_not_found_error(name)}
    skill_dir, fresh_copy = _writable_skill_dir(*found)
    _write_skill_file(skill_dir, fresh_copy, skill_dir / "SKILL.md", content)
    return {"success": True, "message": f"Skill '{name}' updated.", "path": str(skill_dir)}


def _patch_skill(
    name: str,
    old_string: str,
    new_string: str | None,
    file_path: str | None = None,
    replace_all: bool = False,
) -> dict[str, Any]:
    if not old_string:
        return {"success": False, "error": "old_string is required for 'patch'."}
    if new_string is None:
        return {"success": False, "error": "new_string is required for 'patch'."}
    rel_path = file_path or "SKILL.md"
    if rel_path != "SKILL.md" and (err := _validate_file_path(rel_path)):
        return {"success": False, "error": err}
    if not (found := _find_skill(name)):
        return {"success": False, "error": _skill_not_found_error(name)}
    source = found[1] / rel_path
    if err := validate_within_dir(source, found[1]):
        return {"success": False, "error": err}
    if not source.exists():
        return {"success": False, "error": f"File not found: {rel_path}"}
    content = source.read_text(encoding="utf-8")
    new_content, match_count, _, match_error = fuzzy_find_and_replace(content, old_string, new_string, replace_all)
    if match_error:
        return {
            "success": False,
            "error": match_error + format_no_match_hint(match_error, match_count, old_string, content),
            "file_preview": content[:500] + ("..." if len(content) > 500 else ""),
        }
    if err := _validate_content_size(new_content, label=rel_path):
        return {"success": False, "error": err}
    if rel_path == "SKILL.md" and (err := _validate_frontmatter(new_content, Path(name).name)):
        return {"success": False, "error": f"Patch would break SKILL.md structure: {err}"}
    skill_dir, fresh_copy = _writable_skill_dir(*found)
    _write_skill_file(skill_dir, fresh_copy, skill_dir / rel_path, new_content)
    return {
        "success": True,
        "message": f"Patched {rel_path} in skill '{name}' ({match_count} replacement{'s' if match_count > 1 else ''}).",
    }


def _delete_skill(name: str, absorbed_into: str | None = None) -> dict[str, Any]:
    if not (found := _find_skill(name)):
        return {"success": False, "error": _skill_not_found_error(name)}
    target_name = absorbed_into.strip() if absorbed_into else ""
    if target_name:
        if target_name == name:
            return {"success": False, "error": "absorbed_into cannot equal the skill being deleted."}
        if not _find_skill(target_name):
            return {
                "success": False,
                "error": f"absorbed_into='{target_name}' does not exist. Create or patch it first.",
            }
    skill_dir = found[1]
    if not _in_scope(skill_dir):
        return {
            "success": False,
            "error": f"Skill '{name}' is shared and cannot be deleted; only skills saved for the current preset can be deleted.",
        }
    shutil.rmtree(skill_dir)
    if (parent := skill_dir.parent) != found[0] and not any(parent.iterdir()):
        parent.rmdir()
    msg = f"Skill '{name}' deleted."
    if target_name:
        msg += f" Content absorbed into '{target_name}'."
    return {"success": True, "message": msg}


def _write_file(name: str, file_path: str, file_content: str | None) -> dict[str, Any]:
    if err := _validate_file_path(file_path):
        return {"success": False, "error": err}
    if file_content is None:
        return {"success": False, "error": "file_content is required."}
    if err := _validate_content_size(file_content, label=file_path):
        return {"success": False, "error": err}
    if not (found := _find_skill(name)):
        return {"success": False, "error": _skill_not_found_error(name, " Create it first with action='create'.")}
    if err := validate_within_dir(found[1] / file_path, found[1]):
        return {"success": False, "error": err}
    skill_dir, fresh_copy = _writable_skill_dir(*found)
    target = skill_dir / file_path
    _write_skill_file(skill_dir, fresh_copy, target, file_content)
    return {"success": True, "message": f"File '{file_path}' written to skill '{name}'.", "path": str(target)}


def _remove_file(name: str, file_path: str) -> dict[str, Any]:
    if err := _validate_file_path(file_path):
        return {"success": False, "error": err}
    if not (found := _find_skill(name)):
        return {"success": False, "error": _skill_not_found_error(name)}
    source_dir = found[1]
    if err := validate_within_dir(source_dir / file_path, source_dir):
        return {"success": False, "error": err}
    if not (source_dir / file_path).exists():
        avail = [
            f.relative_to(source_dir).as_posix()
            for s in sorted(ALLOWED_SUBDIRS)
            if (d := source_dir / s).exists()
            for f in d.rglob("*")
            if f.is_file()
        ]
        return {
            "success": False,
            "error": f"File '{file_path}' not found in skill '{name}'.",
            "available_files": avail if avail else None,
        }
    skill_dir, fresh_copy = _writable_skill_dir(*found)
    target = skill_dir / file_path
    try:
        target.unlink()
        if (parent := target.parent) != skill_dir and not any(parent.iterdir()):
            parent.rmdir()
    except BaseException:
        if fresh_copy:
            _rollback_new_copy(skill_dir)
        raise
    return {"success": True, "message": f"File '{file_path}' removed from skill '{name}'."}


def skill_manage(
    action: str,
    name: str,
    content: str | None = None,
    category: str | None = None,
    file_path: str | None = None,
    file_content: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    replace_all: bool = False,
    absorbed_into: str | None = None,
) -> str:
    learned_skills_root()
    try:
        if action == "create":
            if not content:
                return tool_error("content is required for 'create'. Provide the full SKILL.md text.", success=False)
            result = _create_skill(name, content, category)
        elif action == "edit":
            if not content:
                return tool_error(
                    "content is required for 'edit'. Provide the full updated SKILL.md text.",
                    success=False,
                )
            result = _edit_skill(name, content)
        elif action == "patch":
            result = _patch_skill(name, old_string or "", new_string, file_path, replace_all)
        elif action == "delete":
            result = _delete_skill(name, absorbed_into=absorbed_into)
        elif action == "write_file":
            result = _write_file(name, file_path or "", file_content)
        elif action == "remove_file":
            result = _remove_file(name, file_path or "")
        else:
            result = {"success": False, "error": f"Unknown action '{action}'."}
    except ValueError as exc:
        return tool_error(str(exc), success=False)

    return json.dumps(result, ensure_ascii=False)


SKILL_MANAGE_SCHEMA = {
    "name": "skill_manage",
    "description": (
        "Manage skills (create, update, delete). Skills are your procedural "
        "memory — reusable approaches for recurring task types. "
        "New skills belong to the current preset; shared skills are copied before being "
        "modified wherever they live.\n\n"
        "Actions: create (full SKILL.md + optional category), "
        "patch (old_string/new_string — preferred for fixes), "
        "edit (full SKILL.md rewrite — major overhauls only), "
        "delete, write_file, remove_file.\n\n"
        "On delete, pass `absorbed_into=<umbrella>` when you're merging this "
        "skill's content into another one, or `absorbed_into=\"\"` when you're "
        "pruning it with no forwarding target. The target you name in "
        "`absorbed_into` must already exist — create/patch the umbrella first, "
        "then delete.\n\n"
        "Create when a verified procedure has concrete future reuse, including a useful correction "
        "or a procedure the user explicitly asks to retain. Tool-call counts and task difficulty "
        "alone do not justify a skill. Check existing skills first to avoid duplicates.\n"
        "Update when: instructions stale/wrong, OS-specific failures, "
        "missing steps or pitfalls found during use. "
        "Verify the cause and successful correction before recording it as a reusable rule.\n\n"
        "Skip one-off details, secrets, user biography and unverified fixes. Create and revise useful "
        "procedures within the current scope without turning routine learning into a confirmation step. "
        "For destructive removal, obtain authorization unless already given; do not remove unrelated content.\n\n"
        "Good skills: trigger conditions, numbered steps with exact commands, "
        "pitfalls section, verification steps. Use skill_view() to see format examples."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "patch", "edit", "delete", "write_file", "remove_file"],
                "description": "The action to perform.",
            },
            "name": {
                "type": "string",
                "description": (
                    "Skill name (lowercase, hyphens/underscores, max 64 chars). Must match an existing skill for "
                    "patch/edit/delete/write_file/remove_file; use 'category/skill-name' for ambiguous names."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "Full SKILL.md content (YAML frontmatter + markdown body); the frontmatter "
                    "must include 'name' equal to the skill name and a 'description'. "
                    "Required for 'create' and 'edit'. For 'edit', read the skill "
                    "first with skill_view() and provide the complete updated text."
                ),
            },
            "old_string": {
                "type": "string",
                "description": (
                    "Text to find in the file (required for 'patch'). Must be unique unless replace_all=true. Include enough surrounding context to ensure uniqueness."
                ),
            },
            "new_string": {
                "type": "string",
                "description": (
                    "Replacement text (required for 'patch'). Can be empty string to delete the matched text."
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": "For 'patch': replace all occurrences instead of requiring a unique match (default: false).",
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional category/domain for organizing the skill (e.g., 'devops', 'data-science', 'mlops'). Creates a subdirectory grouping. Only used with 'create'."
                ),
            },
            "file_path": {
                "type": "string",
                "description": (
                    "Path to a supporting file within the skill directory. "
                    "For 'write_file'/'remove_file': required, must be under references/, "
                    "templates/, scripts/, or assets/. "
                    "For 'patch': optional, defaults to SKILL.md if omitted."
                ),
            },
            "file_content": {"type": "string", "description": "Content for the file. Required for 'write_file'."},
            "absorbed_into": {
                "type": "string",
                "description": (
                    "For 'delete' only — declares merge intent. "
                    "Pass the umbrella skill name when this skill's content "
                    "was merged into another (the target must already exist). "
                    "Pass an empty string when the skill is truly stale and "
                    "being pruned with no forwarding target."
                ),
            },
        },
        "required": ["action", "name"],
    },
}


def _skill_manage_handler(args: dict[str, Any], **kw: Any) -> str:
    # interrupt 提前返回：create 会写盘，避免过期调用覆盖刚编辑的文件。
    if is_interrupted():
        return json.dumps({"error": "Interrupted", "interrupted": True})
    with _mutation_lock:
        if is_interrupted():
            return json.dumps({"error": "Interrupted", "interrupted": True})
        return skill_manage(
            action=args.get("action", ""),
            name=args.get("name", ""),
            content=args.get("content"),
            category=args.get("category"),
            file_path=args.get("file_path"),
            file_content=args.get("file_content"),
            old_string=args.get("old_string"),
            new_string=args.get("new_string"),
            replace_all=args.get("replace_all", False),
            absorbed_into=args.get("absorbed_into"),
        )


registry.register_tool("skill_manage", schema=SKILL_MANAGE_SCHEMA)(_skill_manage_handler)
