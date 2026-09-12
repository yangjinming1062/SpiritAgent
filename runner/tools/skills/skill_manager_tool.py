import contextlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from utils import (
    atomic_replace,
    cfg_get,
    get_skills_dir,
    has_traversal_component,
    is_interrupted,
    is_truthy_value,
    learned_skills_root,
    load_config,
    validate_within_dir,
    visible_skill_path,
    visible_skill_roots,
)

from ..files import format_no_match_hint, fuzzy_find_and_replace
from ..registry import registry, tool_error
from .helpers import is_excluded_skill_path
from .skills_guard import format_scan_report, scan_skill, should_allow_install

logger = logging.getLogger(__name__)
_GUARD_AVAILABLE = True


def get_all_skills_dirs() -> list[Path]:
    return visible_skill_roots()


def _guard_agent_created_enabled() -> bool:
    try:
        return is_truthy_value(cfg_get(load_config(), "skills", "guard_agent_created"), default=False)
    except Exception:
        return False


def _security_scan_skill(skill_dir: Path) -> str | None:
    if not _GUARD_AVAILABLE or not _guard_agent_created_enabled():
        return None
    try:
        result = scan_skill(skill_dir, source="agent-created")
        allowed, reason = should_allow_install(result)
        if allowed is False or allowed is None:
            return f"Security scan blocked this skill ({reason}):\n{format_scan_report(result)}"
    except Exception as e:
        logger.warning("Security scan failed for %s: %s", skill_dir, e, exc_info=True)
    return None


SKILLS_DIR = get_skills_dir()
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024


def _containing_skills_root(skill_path: Path) -> Path:
    try:
        resolved = skill_path.resolve()
    except OSError:
        resolved = skill_path
    for root in get_all_skills_dirs():
        try:
            resolved.relative_to(root.resolve())
            return root
        except (ValueError, OSError):
            continue
    return SKILLS_DIR


MAX_SKILL_CONTENT_CHARS = 100_000
MAX_SKILL_FILE_BYTES = 1_048_576
VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}


def _validate_name(name: str) -> str | None:
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME_RE.match(name):
        return f"Invalid skill name '{name}'. Use lowercase letters, numbers, hyphens, dots, and underscores. Must start with a letter or digit."
    return None


def _validate_category(category: str | None) -> str | None:
    if category is None or not (category := category.strip()):
        return None
    if "/" in category or "\\" in category or len(category) > MAX_NAME_LENGTH or not VALID_NAME_RE.match(category):
        return f"Invalid category '{category}'. Use lowercase letters, numbers, hyphens, dots, and underscores. Categories must be a single directory name."
    return None


def _validate_frontmatter(content: str) -> str | None:
    if not content.strip():
        return "Content cannot be empty."
    if not content.startswith("---"):
        return "SKILL.md must start with YAML frontmatter (---). See existing skills for format."
    if not (end_match := re.search(r"\n---\s*\n", content[3:])):
        return "SKILL.md frontmatter is not closed. Ensure you have a closing '---' line."
    try:
        parsed = yaml.safe_load(content[3 : end_match.start() + 3])
    except yaml.YAMLError as e:
        return f"YAML frontmatter parse error: {e}"
    if not isinstance(parsed, dict):
        return "Frontmatter must be a YAML mapping (key: value pairs)."
    if "name" not in parsed:
        return "Frontmatter must include 'name' field."
    if "description" not in parsed:
        return "Frontmatter must include 'description' field."
    if len(str(parsed["description"])) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    if not content[end_match.end() + 3 :].strip():
        return "SKILL.md must have content after the frontmatter (instructions, procedures, etc.)."
    return None


def _validate_content_size(content: str, label: str = "SKILL.md") -> str | None:
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return f"{label} content is {len(content):,} characters (limit: {MAX_SKILL_CONTENT_CHARS:,}). Consider splitting into a smaller SKILL.md with supporting files in references/ or templates/."
    return None


def _resolve_skill_dir(name: str, category: str | None = None) -> Path:
    root = learned_skills_root()
    target = root / category / name if category else root / name
    if not visible_skill_path(target, root):
        raise ValueError("Skill path escapes its scope")
    return target


def _find_skill(name: str, *, writable: bool = True) -> dict[str, Any] | None:
    for skills_dir in get_all_skills_dirs():
        if skills_dir.exists():
            for skill_md in skills_dir.rglob("SKILL.md"):
                if (
                    not is_excluded_skill_path(skill_md)
                    and skill_md.parent.name == name
                    and visible_skill_path(skill_md, skills_dir)
                ):
                    target = skill_md.parent
                    if writable and not target.resolve().is_relative_to(learned_skills_root().resolve()):
                        target = _resolve_skill_dir(name)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if any(
                            path.is_symlink() or not visible_skill_path(path, skill_md.parent)
                            for path in skill_md.parent.rglob("*")
                        ):
                            raise ValueError("Cannot copy a shared skill containing symlinks")
                        if not target.exists():
                            shutil.copytree(skill_md.parent, target)
                    return {"path": target}
    return None


def _skill_not_found_error(name: str, suffix: str = "") -> str:
    base = f"Skill '{name}' not found. Use skills_list() to see available skills."
    return base + suffix if suffix else base


def _validate_file_path(file_path: str) -> str | None:
    if not file_path:
        return "file_path is required."
    if has_traversal_component(file_path):
        return "Path traversal ('..') is not allowed."
    normalized = Path(file_path)
    if normalized.parts and normalized.name == "SKILL.md" and len(normalized.parts) in {1, 2}:
        return None
    if not normalized.parts or normalized.parts[0] not in ALLOWED_SUBDIRS:
        return f"File must be under one of: {', '.join(sorted(ALLOWED_SUBDIRS))}. Got: '{file_path}'"
    return (
        f"Provide a file path, not just a directory. Example: '{normalized.parts[0]}/myfile.md'"
        if len(normalized.parts) < 2
        else None
    )


def _resolve_skill_target(skill_dir: Path, file_path: str) -> tuple[Path | None, str | None]:
    target = skill_dir / file_path
    if error := validate_within_dir(target, skill_dir):
        return None, error
    return target, None


def _create_skill(name: str, content: str, category: str | None = None) -> dict[str, Any]:
    if err := _validate_name(name):
        return {"success": False, "error": err}
    if err := _validate_category(category):
        return {"success": False, "error": err}
    if err := _validate_frontmatter(content):
        return {"success": False, "error": err}
    if err := _validate_content_size(content):
        return {"success": False, "error": err}
    if _find_skill(name, writable=False):
        return {"success": False, "error": f"A skill named '{name}' already exists."}
    skill_dir = _resolve_skill_dir(name, category)
    skill_dir.mkdir(parents=True, exist_ok=True)
    atomic_replace(str(skill_dir / "SKILL.md"), content)
    if scan_error := _security_scan_skill(skill_dir):
        shutil.rmtree(skill_dir, ignore_errors=True)
        return {"success": False, "error": scan_error}
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
    if err := _validate_frontmatter(content):
        return {"success": False, "error": err}
    if err := _validate_content_size(content):
        return {"success": False, "error": err}
    if not (existing := _find_skill(name)):
        return {"success": False, "error": _skill_not_found_error(name)}
    skill_md = existing["path"] / "SKILL.md"
    orig = skill_md.read_text(encoding="utf-8") if skill_md.exists() else None
    atomic_replace(str(skill_md), content)
    if scan_err := _security_scan_skill(existing["path"]):
        if orig is not None:
            atomic_replace(str(skill_md), orig)
        return {"success": False, "error": scan_err}
    return {"success": True, "message": f"Skill '{name}' updated.", "path": str(existing["path"])}


def _patch_skill(
    name: str,
    old_string: str,
    new_string: str,
    file_path: str | None = None,
    replace_all: bool = False,
) -> dict[str, Any]:
    if not old_string:
        return {"success": False, "error": "old_string is required for 'patch'."}
    if new_string is None:
        return {"success": False, "error": "new_string is required for 'patch'."}
    if not (existing := _find_skill(name)):
        return {"success": False, "error": _skill_not_found_error(name)}
    skill_dir = existing["path"]
    if file_path:
        if err := _validate_file_path(file_path):
            return {"success": False, "error": err}
        if not (target := _resolve_skill_target(skill_dir, file_path)[0]):
            return {"success": False, "error": _resolve_skill_target(skill_dir, file_path)[1]}
    else:
        target = skill_dir / "SKILL.md"
    if not target.exists():
        return {"success": False, "error": f"File not found: {target.relative_to(skill_dir)}"}
    content = target.read_text(encoding="utf-8")
    new_content, match_count, _, match_error = fuzzy_find_and_replace(content, old_string, new_string, replace_all)
    if match_error:
        with contextlib.suppress(Exception):
            match_error += format_no_match_hint(match_error, match_count, old_string, content)
        return {
            "success": False,
            "error": match_error,
            "file_preview": content[:500] + ("..." if len(content) > 500 else ""),
        }
    if err := _validate_content_size(new_content, label=file_path if file_path else "SKILL.md"):
        return {"success": False, "error": err}
    if not file_path and (err := _validate_frontmatter(new_content)):
        return {"success": False, "error": f"Patch would break SKILL.md structure: {err}"}
    orig = content
    atomic_replace(str(target), new_content)
    if scan_err := _security_scan_skill(skill_dir):
        atomic_replace(str(target), orig)
        return {"success": False, "error": scan_err}
    return {
        "success": True,
        "message": f"Patched {file_path if file_path else 'SKILL.md'} in skill '{name}' ({match_count} replacement{'s' if match_count > 1 else ''}).",
    }


def _delete_skill(name: str, absorbed_into: str | None = None) -> dict[str, Any]:
    if not (existing := _find_skill(name, writable=False)):
        return {"success": False, "error": _skill_not_found_error(name)}
    if absorbed_into and absorbed_into.strip():
        t_name = absorbed_into.strip()
        if t_name == name:
            return {"success": False, "error": "absorbed_into cannot equal the skill being deleted."}
        if not _find_skill(t_name, writable=False):
            return {"success": False, "error": f"absorbed_into='{t_name}' does not exist. Create or patch it first."}
    skill_dir = existing["path"]
    if not skill_dir.resolve().is_relative_to(learned_skills_root().resolve()):
        return {"success": False, "error": "Shared static skills cannot be deleted by a model"}
    skills_root = _containing_skills_root(skill_dir)
    shutil.rmtree(skill_dir)
    if (parent := skill_dir.parent) != skills_root and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    msg = f"Skill '{name}' deleted."
    if absorbed_into and absorbed_into.strip():
        msg += f" Content absorbed into '{absorbed_into.strip()}'."
    return {"success": True, "message": msg}


def _write_file(name: str, file_path: str, file_content: str) -> dict[str, Any]:
    if err := _validate_file_path(file_path):
        return {"success": False, "error": err}
    if file_content is None:
        return {"success": False, "error": "file_content is required."}
    c_bytes = len(file_content.encode("utf-8"))
    if c_bytes > MAX_SKILL_FILE_BYTES:
        return {
            "success": False,
            "error": f"File content is {c_bytes:,} bytes (limit: {MAX_SKILL_FILE_BYTES:,} bytes).",
        }
    if err := _validate_content_size(file_content, label=file_path):
        return {"success": False, "error": err}
    if not (existing := _find_skill(name)):
        return {"success": False, "error": _skill_not_found_error(name, " Create it first with action='create'.")}
    if not (target := _resolve_skill_target(existing["path"], file_path)[0]):
        return {"success": False, "error": _resolve_skill_target(existing["path"], file_path)[1]}
    target.parent.mkdir(parents=True, exist_ok=True)
    orig = target.read_text(encoding="utf-8") if target.exists() else None
    atomic_replace(str(target), file_content)
    if scan_err := _security_scan_skill(existing["path"]):
        if orig is not None:
            atomic_replace(str(target), orig)
        else:
            target.unlink(missing_ok=True)
        return {"success": False, "error": scan_err}
    return {"success": True, "message": f"File '{file_path}' written to skill '{name}'.", "path": str(target)}


def _remove_file(name: str, file_path: str) -> dict[str, Any]:
    if err := _validate_file_path(file_path):
        return {"success": False, "error": err}
    if not (existing := _find_skill(name)):
        return {"success": False, "error": _skill_not_found_error(name)}
    skill_dir = existing["path"]
    if not (target := _resolve_skill_target(skill_dir, file_path)[0]):
        return {"success": False, "error": _resolve_skill_target(skill_dir, file_path)[1]}
    if not target.exists():
        avail = [
            str(f.relative_to(skill_dir))
            for s in ALLOWED_SUBDIRS
            if (d := skill_dir / s).exists()
            for f in d.rglob("*")
            if f.is_file()
        ]
        return {
            "success": False,
            "error": f"File '{file_path}' not found in skill '{name}'.",
            "available_files": avail if avail else None,
        }
    target.unlink()
    if (parent := target.parent) != skill_dir and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
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
    if action == "create":
        if not content:
            return tool_error("content is required for 'create'. Provide the full SKILL.md text.", success=False)
        result = _create_skill(name, content, category)
    elif action == "edit":
        if not content:
            return tool_error("content is required for 'edit'. Provide the full updated SKILL.md text.", success=False)
        result = _edit_skill(name, content)
    elif action == "patch":
        if not old_string:
            return tool_error("old_string is required for 'patch'.", success=False)
        if new_string is None:
            return tool_error("new_string is required for 'patch'.", success=False)
        result = _patch_skill(name, old_string, new_string, file_path, replace_all)
    elif action == "delete":
        result = _delete_skill(name, absorbed_into=absorbed_into)
    elif action == "write_file":
        if not file_path:
            return tool_error("file_path is required for 'write_file'.", success=False)
        if file_content is None:
            return tool_error("file_content is required for 'write_file'.", success=False)
        result = _write_file(name, file_path, file_content)
    elif action == "remove_file":
        if not file_path:
            return tool_error("file_path is required for 'remove_file'.", success=False)
        result = _remove_file(name, file_path)
    else:
        result = {"success": False, "error": f"Unknown action '{action}'."}

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
        "Create when: complex task succeeded (5+ calls), errors overcome, "
        "user-corrected approach worked, non-trivial workflow discovered, "
        "or user asks you to remember a procedure.\n"
        "Update when: instructions stale/wrong, OS-specific failures, "
        "missing steps or pitfalls found during use. "
        "If you used a skill and hit issues not covered by it, patch it immediately.\n\n"
        "After difficult/iterative tasks, offer to save as a skill. "
        "Skip for simple one-offs. Confirm with user before creating/deleting.\n\n"
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
                    "Skill name (lowercase, hyphens/underscores, max 64 chars). Must match an existing skill for patch/edit/delete/write_file/remove_file."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "Full SKILL.md content (YAML frontmatter + markdown body). "
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


# --- 注册表 ---
def _skill_manage_handler(args: dict[str, Any], **kw: Any) -> str:
    # 廉价的 interrupt 提前返回：skill_manage 在 "create" 动作下写磁盘。
    # 没有这个兜底，过期调用可能覆盖刚编辑完的文件。
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
