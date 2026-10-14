import logging
import sys
from pathlib import Path
from typing import Any

from spirit_agent_runner.contracts.interrupt import is_interrupted
from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.skills.helpers import (
    find_skill_candidates,
    get_disabled_skill_names,
    get_skill_description,
    get_spiritagent_metadata,
    iter_skill_files,
    parse_frontmatter,
    skill_lookup_path_error,
)
from spirit_agent_runner.features.skills.scope import visible_skill_roots
from spirit_agent_runner.features.skills.skill_manager_tool import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    MAX_SKILL_FILE_BYTES,
)
from spirit_agent_runner.infrastructure.file_safety import has_traversal_component, validate_within_dir

logger = logging.getLogger(__name__)

_PLATFORM_ALIASES = {"darwin": "macos", "macos": "macos", "win32": "windows", "windows": "windows"}
_HOST_PLATFORM = _PLATFORM_ALIASES.get(sys.platform, sys.platform)


def skill_matches_platform(frontmatter: dict[str, Any]) -> bool:
    """platforms 是否允许当前宿主；空/未声明不限制，darwin/win32 为别名。"""
    declared = frontmatter.get("platforms")
    if declared is None:
        return True
    values = declared if isinstance(declared, list) else [declared]
    if not values:
        return True
    return _HOST_PLATFORM in {_PLATFORM_ALIASES.get(str(v).lower(), str(v).lower()) for v in values}


def _get_category_from_path(skill_path: Path) -> str | None:
    dirs = visible_skill_roots()
    for d in dirs:
        try:
            if len(parts := skill_path.relative_to(d).parts) >= 3:
                return parts[0]
        except ValueError:
            pass
    return None


def _parse_tags(tags_value: Any) -> list[str]:
    if not tags_value:
        return []
    if isinstance(tags_value, list):
        return [str(t).strip() for t in tags_value if t]
    val = str(tags_value).strip()
    if val.startswith("[") and val.endswith("]"):
        val = val[1:-1]
    return [t.strip().strip("\"'") for t in val.split(",") if t.strip()]


def _is_disabled(name: str, category: str | None, disabled: set[str]) -> bool:
    """name 或 category 命中 disabled 即禁用；嵌套 skill 单条 entry 覆盖整夹。"""
    if name in disabled:
        return True
    return bool(category is not None and category in disabled)


def _find_all_skills() -> list[dict[str, Any]]:
    skills = []
    seen_names = set()
    disabled = get_disabled_skill_names()
    for d in visible_skill_roots():
        for skill_md in iter_skill_files(d):
            try:
                frontmatter, body = parse_frontmatter(skill_md.read_text(encoding="utf-8")[:4000])
                if not skill_matches_platform(frontmatter):
                    continue
                raw_name = frontmatter.get("name", skill_md.parent.name)
                if not isinstance(raw_name, str) or not raw_name.strip():
                    # name 必须是非空字符串；非法 name 记 WARNING 后跳过，避免技能静默从列表消失
                    logger.warning(
                        "skills_list: skill at %s has a non-string name (%r); skipping",
                        skill_md.parent,
                        raw_name,
                    )
                    continue
                name = raw_name[:MAX_NAME_LENGTH]
                category = _get_category_from_path(skill_md)
                if name in seen_names or _is_disabled(name, category, disabled):
                    continue

                desc = get_skill_description(frontmatter, body)
                if len(desc) > MAX_DESCRIPTION_LENGTH:
                    desc = desc[: MAX_DESCRIPTION_LENGTH - 3] + "..."

                seen_names.add(name)
                skills.append({"name": name, "description": desc, "category": category})
            except (UnicodeDecodeError, PermissionError) as e:
                logger.debug("Failed to read skill file %s: %s", skill_md, e)
            except Exception as e:
                logger.debug("Skipping skill at %s: failed to parse: %s", skill_md, e, exc_info=True)
    return skills


def _sort_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(skills, key=lambda s: (s.get("category") or "", s["name"]))


def skills_list(category: str | None = None) -> dict[str, Any]:
    try:
        all_skills = _find_all_skills()
        if not all_skills:
            return {"success": True, "skills": [], "categories": [], "message": "No skills found in skills/ directory."}
        if category:
            all_skills = [s for s in all_skills if s.get("category") == category]
        all_skills = _sort_skills(all_skills)
        return {
            "success": True,
            "skills": all_skills,
            "categories": sorted({s["category"] for s in all_skills if s.get("category")}),
            "count": len(all_skills),
            "hint": "Use skill_view(name) to see full content, tags, and linked files",
        }
    except Exception as e:
        return {"error": str(e), "success": False}


def skill_view(name: str, file_path: str | None = None) -> dict[str, Any]:
    try:
        if lookup_error := skill_lookup_path_error(name):
            return {
                "success": False,
                "error": lookup_error,
                "hint": "Use a skill name or relative path within the skills directory.",
            }

        search_root, candidates = find_skill_candidates(name)

        if len(candidates) > 1:
            paths = [str(smd) for smd in candidates]
            logger.warning("Skill name collision for '%s': %d candidates — %s", name, len(candidates), "; ".join(paths))
            return {
                "success": False,
                "error": (
                    f"Ambiguous skill name '{name}': {len(candidates)} skills match. "
                    "Refusing to guess — load one explicitly by its categorized path."
                ),
                "matches": paths,
                "hint": (
                    "Pass the full relative path instead of the bare name "
                    "(e.g., 'category/skill-name'), or rename one of the colliding skills "
                    "so each name is unique."
                ),
            }

        if search_root is None:
            return {
                "success": False,
                "error": f"Skill '{name}' not found.",
                "available_skills": [s["name"] for s in _sort_skills(_find_all_skills())[:20]],
                "hint": "Use skills_list to see all available skills",
            }

        skill_md = candidates[0]
        skill_dir = skill_md.parent

        try:
            content = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return {"success": False, "error": f"Failed to read skill '{name}': {e}"}

        parsed_frontmatter, _ = parse_frontmatter(content)

        if not skill_matches_platform(parsed_frontmatter):
            return {
                "success": False,
                "error": f"Skill '{name}' is not supported on this platform.",
            }

        resolved_name = parsed_frontmatter.get("name", skill_dir.name)
        if _is_disabled(resolved_name, _get_category_from_path(skill_md), get_disabled_skill_names()):
            return {"success": False, "error": f"Skill '{resolved_name}' is disabled in the user's skill settings."}

        if file_path:
            if has_traversal_component(file_path):
                return {
                    "success": False,
                    "error": "Path traversal ('..') is not allowed.",
                    "hint": "Use a relative path within the skill directory",
                }
            target_file = skill_dir / file_path
            if traversal_error := validate_within_dir(target_file, skill_dir):
                return {
                    "success": False,
                    "error": traversal_error,
                    "hint": "Use a relative path within the skill directory",
                }
            if not target_file.exists():
                available_files = {"references": [], "templates": [], "assets": [], "scripts": [], "other": []}
                for f in skill_dir.rglob("*"):
                    if f.is_file() and f.name != "SKILL.md":
                        rel = str(f.relative_to(skill_dir))
                        if rel.startswith("references/"):
                            available_files["references"].append(rel)
                        elif rel.startswith("templates/"):
                            available_files["templates"].append(rel)
                        elif rel.startswith("assets/"):
                            available_files["assets"].append(rel)
                        elif rel.startswith("scripts/"):
                            available_files["scripts"].append(rel)
                        elif f.suffix in {".md", ".py", ".yaml", ".yml", ".json", ".tex", ".sh"}:
                            available_files["other"].append(rel)
                available_files = {k: v for k, v in available_files.items() if v}
                return {
                    "success": False,
                    "error": f"File '{file_path}' not found in skill '{name}'.",
                    "available_files": available_files,
                    "hint": "Use one of the available file paths listed above",
                }
            try:
                # 读端同闸：写端 1 MiB 不能让 view 绕过，防链接文件撑爆上下文。
                file_size = target_file.stat().st_size
                if file_size > MAX_SKILL_FILE_BYTES:
                    return {
                        "success": False,
                        "error": (
                            f"File '{file_path}' is {file_size} bytes, "
                            f"exceeds {MAX_SKILL_FILE_BYTES}-byte skill-file read cap."
                        ),
                        "hint": ("Link a smaller excerpt via patch or split the file outside the skill bundle."),
                    }
                f_content = target_file.read_text(encoding="utf-8")
                return {
                    "success": True,
                    "name": name,
                    "file": file_path,
                    "content": f_content,
                    "file_type": target_file.suffix,
                }
            except UnicodeDecodeError:
                return {
                    "success": True,
                    "name": name,
                    "file": file_path,
                    "content": f"[Binary file: {target_file.name}, size: {target_file.stat().st_size} bytes]",
                    "is_binary": True,
                }

        ref_files, tmp_files, ast_files, scr_files = [], [], [], []
        if (ref_dir := skill_dir / "references").exists():
            ref_files = [str(f.relative_to(skill_dir)) for f in ref_dir.glob("*.md")]
        if (tmp_dir := skill_dir / "templates").exists():
            for ext in ["*.md", "*.py", "*.yaml", "*.yml", "*.json", "*.tex", "*.sh"]:
                tmp_files.extend(str(f.relative_to(skill_dir)) for f in tmp_dir.rglob(ext))
        if (ast_dir := skill_dir / "assets").exists():
            ast_files = [str(f.relative_to(skill_dir)) for f in ast_dir.rglob("*") if f.is_file()]
        if (scr_dir := skill_dir / "scripts").exists():
            for ext in ["*.py", "*.sh", "*.bash", "*.js", "*.ts", "*.rb"]:
                scr_files.extend(str(f.relative_to(skill_dir)) for f in scr_dir.glob(ext))

        spiritagent_meta = get_spiritagent_metadata(parsed_frontmatter)
        tags = _parse_tags(spiritagent_meta.get("tags") or parsed_frontmatter.get("tags", ""))
        related_skills = _parse_tags(
            spiritagent_meta.get("related_skills") or parsed_frontmatter.get("related_skills", ""),
        )

        linked_files = {
            k: v
            for k, v in [
                ("references", ref_files),
                ("templates", tmp_files),
                ("assets", ast_files),
                ("scripts", scr_files),
            ]
            if v
        }

        # path 按技能所在根目录取相对路径（根可能是共享技能目录或当前学习域目录）。
        rel_path = skill_md.relative_to(search_root).as_posix()

        result = {
            "success": True,
            "name": parsed_frontmatter.get("name", skill_md.parent.name),
            "description": get_skill_description(parsed_frontmatter),
            "tags": tags,
            "related_skills": related_skills,
            "content": content,
            "path": rel_path,
            "skill_dir": str(skill_dir),
            "linked_files": linked_files if linked_files else None,
            "usage_hint": (
                "To view linked files, call skill_view(name, file_path) where file_path is "
                "e.g. 'references/api.md' or 'assets/config.yaml'"
            )
            if linked_files
            else None,
        }

        if isinstance(meta := parsed_frontmatter.get("metadata"), dict):
            result["metadata"] = meta

        return result

    except Exception as e:
        return {"error": str(e), "success": False}


SKILLS_LIST_SCHEMA = {
    "name": "skills_list",
    "description": "List available skills (name + description). Use skill_view(name) to load full content.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"category": {"type": "string", "description": "Optional category filter to narrow results"}},
        "required": [],
    },
}

SKILL_VIEW_SCHEMA = {
    "name": "skill_view",
    "description": (
        "Skills allow for loading information about specific tasks and workflows, as well as "
        "scripts and templates. Load a skill's full content or access its linked files "
        "(references, templates, scripts). First call returns SKILL.md content plus a "
        "'linked_files' dict showing available references/templates/scripts. To access those, "
        "call again with file_path parameter."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "The skill name (use skills_list to see available skills). "
                    "If several skills share a name, pass the relative path 'category/skill-name'."
                ),
            },
            "file_path": {
                "type": "string",
                "description": (
                    "OPTIONAL: Path to a linked file within the skill "
                    "(e.g., 'references/api.md', 'templates/config.yaml', 'scripts/validate.py'). "
                    "Omit to get the main SKILL.md content."
                ),
            },
        },
        "required": ["name"],
    },
}


def _skill_view_handler(args: dict[str, Any], **kw: Any) -> dict[str, Any]:
    # interrupt 提前返回：避免过期列表调用在用户已转移注意力后继续读盘。
    if is_interrupted():
        return {"error": "Interrupted", "interrupted": True}
    return skill_view(name=args.get("name", ""), file_path=args.get("file_path"))


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="skills_list",
            toolset_id="skills_system",
            schema=SKILLS_LIST_SCHEMA,
            handler=lambda _context, args: skills_list(category=args.get("category")),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="skill_view",
            toolset_id="skills_system",
            schema=SKILL_VIEW_SCHEMA,
            handler=lambda context, args: _skill_view_handler(
                args,
                task_id=context.task_id,
                cancel_token=context.cancel_event,
            ),
            check_fn=None,
            is_async=False,
        ),
    ]
