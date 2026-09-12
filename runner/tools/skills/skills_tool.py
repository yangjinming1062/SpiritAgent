import contextlib
import json
import logging
import re
import sys
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from utils import (
    cfg_get,
    get_env_type,
    get_skills_dir,
    has_traversal_component,
    is_interrupted,
    load_config,
    register_credential_file,
    register_env_passthrough,
    validate_within_dir,
    visible_skill_path,
    visible_skill_roots,
)

from ..registry import registry, tool_error
from .helpers import (
    get_disabled_skill_names,
    get_spiritagent_metadata,
    is_excluded_skill_path,
    iter_skill_index_files,
    parse_frontmatter,
)
from .skill_manager_tool import MAX_SKILL_FILE_BYTES

logger = logging.getLogger(__name__)

SKILLS_DIR = get_skills_dir()

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

_PLATFORM_MAP = {"macos": "darwin", "windows": "win32"}
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REMOTE_ENV_BACKENDS = frozenset({"ssh"})


def _skill_lookup_path_error(name: str) -> str | None:
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


class SkillReadinessStatus(StrEnum):
    AVAILABLE = "available"
    SETUP_NEEDED = "setup_needed"
    UNSUPPORTED = "unsupported"


_INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous",
    "you are now",
    "disregard your",
    "forget your instructions",
    "new instructions:",
    "system prompt:",
    "<system>",
    "]]>",
]


def skill_matches_platform(frontmatter: dict[str, Any]) -> bool:
    """当 skill 的 platforms 字段允许当前 OS 时返回 True。

    runner 仅在启动它的宿主上运行，所以唯一有意义的检查是：是否列出任何平台，若是，当前宿主是否在其中。
    缺失 / 列表为空表示"所有平台"。

    frontmatter 取值为 macos / windows。_PLATFORM_MAP 把每个声明值翻译成 sys.platform 必须匹配的字符串
    （darwin / win32）。
    没有这个翻译，OS 字符串永远不匹配 — 例如 macOS 专属的 skill 会在唯一能运行它们的 OS 上被过滤掉。
    """
    declared = frontmatter.get("platforms") or frontmatter.get("platform")
    if not declared:
        return True
    if isinstance(declared, str):
        declared = [declared]
    if not isinstance(declared, list):
        return True
    allowed_os = [_PLATFORM_MAP.get(str(p).lower(), str(p).lower()) for p in declared]
    return sys.platform in allowed_os


def _normalize_setup_metadata(frontmatter: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(setup := frontmatter.get("setup"), dict):
        return {"help": None, "collect_secrets": []}

    help_text = setup.get("help")
    raw_secrets = setup.get("collect_secrets")
    raw_secrets = (
        [raw_secrets] if isinstance(raw_secrets, dict) else (raw_secrets if isinstance(raw_secrets, list) else [])
    )

    collect_secrets = []
    for item in raw_secrets:
        if isinstance(item, dict) and (env_var := str(item.get("env_var") or "").strip()):
            entry = {
                "env_var": env_var,
                "prompt": str(item.get("prompt") or f"Enter value for {env_var}").strip(),
                "secret": bool(item.get("secret", True)),
            }
            if provider_url := str(item.get("provider_url") or item.get("url") or "").strip():
                entry["provider_url"] = provider_url
            collect_secrets.append(entry)

    return {
        "help": str(help_text).strip() if isinstance(help_text, str) and help_text.strip() else None,
        "collect_secrets": collect_secrets,
    }


def _get_required_environment_variables(frontmatter: dict[str, Any]) -> list[dict[str, Any]]:
    setup = _normalize_setup_metadata(frontmatter)
    required_raw = frontmatter.get("required_environment_variables")
    items = (
        [required_raw] if isinstance(required_raw, dict) else (required_raw if isinstance(required_raw, list) else [])
    )

    required: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append_required(entry: dict[str, Any]) -> None:
        env_name = str(entry.get("name") or entry.get("env_var") or "").strip()
        if not env_name or env_name in seen or not _ENV_VAR_NAME_RE.match(env_name):
            return
        normalized = {"name": env_name, "prompt": str(entry.get("prompt") or f"Enter value for {env_name}").strip()}
        if (
            (h := entry.get("help") or entry.get("provider_url") or entry.get("url") or setup.get("help"))
            and isinstance(h, str)
            and h.strip()
        ):
            normalized["help"] = h.strip()
        if (rf := entry.get("required_for")) and isinstance(rf, str) and rf.strip():
            normalized["required_for"] = rf.strip()
        if entry.get("optional"):
            normalized["optional"] = True
        seen.add(env_name)
        required.append(normalized)

    for item in items:
        if isinstance(item, str):
            _append_required({"name": item})
        elif isinstance(item, dict):
            _append_required(item)

    for item in setup["collect_secrets"]:
        _append_required(
            {
                "name": item.get("env_var"),
                "prompt": item.get("prompt"),
                "help": item.get("provider_url") or setup.get("help"),
            },
        )

    return required


def _env_overrides() -> dict[str, str]:
    """从内存配置中读取 skills.env_overrides map。

    runner 不通过交互式提示收集密钥。运维在 Runner 配置中声明每个 skill 的 env 值；Desktop 把该 map 暴露给用户。
    缺失条目会让 skill 抛 setup_needed 提示而非失败。
    """
    overrides = cfg_get(load_config(), "skills", "env_overrides", default={})
    return overrides if isinstance(overrides, dict) else {}


def _build_setup_note(
    readiness_status: SkillReadinessStatus,
    missing: list[str],
    setup_help: str | None = None,
) -> str | None:
    if readiness_status == SkillReadinessStatus.SETUP_NEEDED:
        note = (
            f"Setup needed before using this skill: missing "
            f"{', '.join(missing) if missing else 'required prerequisites'}."
        )
        return f"{note} {setup_help}" if setup_help else note
    return None


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
    """纯成员检查：若 name 或 category 在 disabled 集合中则视为 disabled。
    顶层 skill（category=None）仅按 name 匹配；嵌套 skill 按 name 或 category 匹配，
    使单条 entry 覆盖该文件夹下每个 SKILL.md。
    """
    if name in disabled:
        return True
    return bool(category is not None and category in disabled)


def _is_skill_disabled(name: str, category: str | None = None, platform: str | None = None) -> bool:
    """为 _is_disabled 包一层配置加载。同时尊重全局 skills.disabled 列表（按 name 或 category 匹配）以及
    按平台的 skills.platform_disabled[plat] map（仅按 name 匹配）。当 platform map 已定义时短路掉全局
    列表 — 与原始实现的"二选一"语义保持一致。

    已经持有预加载 disabled 集合的调用方（例如 _find_all_skills）应直接调用 _is_disabled 以避免重复解析。
    """
    try:
        cfg = cfg_get(load_config(), "skills", default={})
        plat = platform or sys.platform
        p_dis = cfg_get(cfg, "platform_disabled", plat)
        if isinstance(p_dis, list):
            return name in {str(n) for n in p_dis}
        return _is_disabled(name, category, get_disabled_skill_names())
    except Exception:
        return False


def _find_all_skills(*, skip_disabled: bool = False) -> list[dict[str, Any]]:
    skills = []
    seen_names = set()
    disabled = set() if skip_disabled else get_disabled_skill_names()
    dirs = visible_skill_roots()
    for d in dirs:
        for skill_md in iter_skill_index_files(d, "SKILL.md"):
            if is_excluded_skill_path(skill_md) or not visible_skill_path(skill_md, d):
                continue
            try:
                frontmatter, body = parse_frontmatter(skill_md.read_text(encoding="utf-8")[:4000])
                if not skill_matches_platform(frontmatter):
                    continue
                raw_name = frontmatter.get("name", skill_md.parent.name)
                if not isinstance(raw_name, str) or not raw_name.strip():
                    # YAML 中 name: 123（或 list）以前会在此处抛错，skill 静默从列表中消失，仅留 debug 日志 —
                    # 改为显式提示
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

                desc = frontmatter.get("description", "")
                if not desc:
                    desc = next(
                        (
                            line.strip()
                            for line in body.strip().split("\n")
                            if line.strip() and not line.strip().startswith("#")
                        ),
                        "",
                    )
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


def skills_list(category: str | None = None, task_id: str | None = None) -> str:
    try:
        all_skills = _find_all_skills()
        if not all_skills:
            return json.dumps(
                {"success": True, "skills": [], "categories": [], "message": "No skills found in skills/ directory."},
                ensure_ascii=False,
            )
        if category:
            all_skills = [s for s in all_skills if s.get("category") == category]
        all_skills = _sort_skills(all_skills)
        return json.dumps(
            {
                "success": True,
                "skills": all_skills,
                "categories": sorted({s["category"] for s in all_skills if s.get("category")}),
                "count": len(all_skills),
                "hint": "Use skill_view(name) to see full content, tags, and linked files",
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return tool_error(str(e), success=False)


def skill_view(name: str, file_path: str | None = None, task_id: str | None = None) -> str:
    try:
        if lookup_error := _skill_lookup_path_error(name):
            return json.dumps(
                {
                    "success": False,
                    "error": lookup_error,
                    "hint": "Use a skill name or relative path within the skills directory.",
                },
                ensure_ascii=False,
            )

        local_category_name = None
        if ":" in name:
            namespace, _, bare = name.partition(":")
            if bare:
                local_category_name = f"{namespace}/{bare}"

        if local_category_name and (err := _skill_lookup_path_error(local_category_name)):
            return json.dumps(
                {
                    "success": False,
                    "error": err,
                    "hint": "Use a skill name or relative path within the skills directory.",
                },
                ensure_ascii=False,
            )

        all_dirs = visible_skill_roots()
        if not all_dirs:
            return json.dumps(
                {
                    "success": False,
                    "error": "Skills directory does not exist yet. It will be created on first install.",
                },
                ensure_ascii=False,
            )

        candidates = []
        seen_md = set()

        def _record(sd: Path | None, smd: Path) -> None:
            if not visible_skill_path(smd, search_dir):
                return
            try:
                key = smd.resolve()
            except Exception:
                key = smd
            if key not in seen_md:
                seen_md.add(key)
                candidates.append((sd, smd))

        for search_dir in all_dirs:
            if candidates:
                break
            direct = search_dir / name
            if direct.is_dir() and (direct / "SKILL.md").exists():
                _record(direct, direct / "SKILL.md")
            elif direct.with_suffix(".md").exists():
                _record(None, direct.with_suffix(".md"))
            if local_category_name:
                cat_p = search_dir / local_category_name
                if cat_p.is_dir() and (cat_p / "SKILL.md").exists():
                    _record(cat_p, cat_p / "SKILL.md")
                elif cat_p.with_suffix(".md").exists():
                    _record(None, cat_p.with_suffix(".md"))
            for fmd in iter_skill_index_files(search_dir, "SKILL.md"):
                if fmd.parent.name == name:
                    _record(fmd.parent, fmd)
            for fmd in search_dir.rglob(f"{name}.md"):
                if fmd.name != "SKILL.md":
                    _record(None, fmd)

        if len(candidates) > 1:
            paths = [str(smd) for _, smd in candidates]
            logger.warning("Skill name collision for '%s': %d candidates — %s", name, len(candidates), "; ".join(paths))
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        f"Ambiguous skill name '{name}': {len(candidates)} skills match "
                        "across your local skills dir and external_dirs. "
                        "Refusing to guess — load one explicitly by its categorized path."
                    ),
                    "matches": paths,
                    "hint": (
                        "Pass the full relative path instead of the bare name "
                        "(e.g., 'category/skill-name'), or rename one of the colliding skills "
                        "so each name is unique."
                    ),
                },
                ensure_ascii=False,
            )

        if not candidates or not (skill_md := candidates[0][1]).exists():
            return json.dumps(
                {
                    "success": False,
                    "error": f"Skill '{name}' not found.",
                    "available_skills": [s["name"] for s in _sort_skills(_find_all_skills())[:20]],
                    "hint": "Use skills_list to see all available skills",
                },
                ensure_ascii=False,
            )

        skill_dir = candidates[0][0]

        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as e:
            return json.dumps({"success": False, "error": f"Failed to read skill '{name}': {e}"}, ensure_ascii=False)

        outside = not any(visible_skill_path(skill_md, root) for root in all_dirs)
        inj = any(p in content.lower() for p in _INJECTION_PATTERNS)
        if outside or inj:
            warns = []
            if outside:
                warns.append(f"skill file is outside the trusted skills directory (~/.spiritagent/skills/): {skill_md}")
            if inj:
                warns.append("skill content contains patterns that may indicate prompt injection")
            logger.warning("Skill security warning for '%s': %s", name, "; ".join(warns))
            # 不可信 skill 在 view 阶段必须硬阻断, 不能只警告: 一个 community skill 若已绕过安装期扫描,
            # 后续 view 不再防御就是把注入内容直灌 LLM 上下文。Builtin / 内部 skill 只警告不阻断。
            if outside or inj:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Skill '{name}' blocked by skill_view security gate: " + "; ".join(warns),
                        "skill_source": "external" if outside else "internal-but-suspicious",
                    },
                    ensure_ascii=False,
                )

        parsed_frontmatter = {}
        with contextlib.suppress(Exception):
            parsed_frontmatter, _ = parse_frontmatter(content)

        if not skill_matches_platform(parsed_frontmatter):
            return json.dumps(
                {
                    "success": False,
                    "error": f"Skill '{name}' is not supported on this platform.",
                    "readiness_status": SkillReadinessStatus.UNSUPPORTED.value,
                },
                ensure_ascii=False,
            )

        resolved_name = parsed_frontmatter.get("name", skill_md.parent.name)
        if _is_skill_disabled(resolved_name, category=_get_category_from_path(skill_md)):
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        f"Skill '{resolved_name}' is disabled. Enable it with "
                        "`spiritagent skills` or inspect the files directly on disk."
                    ),
                },
                ensure_ascii=False,
            )

        if file_path and skill_dir:
            if has_traversal_component(file_path):
                return json.dumps(
                    {
                        "success": False,
                        "error": "Path traversal ('..') is not allowed.",
                        "hint": "Use a relative path within the skill directory",
                    },
                    ensure_ascii=False,
                )
            target_file = skill_dir / file_path
            if traversal_error := validate_within_dir(target_file, skill_dir):
                return json.dumps(
                    {
                        "success": False,
                        "error": traversal_error,
                        "hint": "Use a relative path within the skill directory",
                    },
                    ensure_ascii=False,
                )
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
                return json.dumps(
                    {
                        "success": False,
                        "error": f"File '{file_path}' not found in skill '{name}'.",
                        "available_files": available_files,
                        "hint": "Use one of the available file paths listed above",
                    },
                    ensure_ascii=False,
                )
            try:
                # 读端也要套同一道闸: 写端 1 MiB 上限不能让 view 端绕过,
                # 否则攻击者可以塞超过模型上下文上限的链接文件撑爆对话。
                file_size = target_file.stat().st_size
                if file_size > MAX_SKILL_FILE_BYTES:
                    return json.dumps(
                        {
                            "success": False,
                            "error": (
                                f"File '{file_path}' is {file_size} bytes, "
                                f"exceeds {MAX_SKILL_FILE_BYTES}-byte skill-file read cap."
                            ),
                            "hint": ("Link a smaller excerpt via patch or split the file outside the skill bundle."),
                        },
                        ensure_ascii=False,
                    )
                f_content = target_file.read_text(encoding="utf-8")
                return json.dumps(
                    {
                        "success": True,
                        "name": name,
                        "file": file_path,
                        "content": f_content,
                        "file_type": target_file.suffix,
                    },
                    ensure_ascii=False,
                )
            except UnicodeDecodeError:
                return json.dumps(
                    {
                        "success": True,
                        "name": name,
                        "file": file_path,
                        "content": f"[Binary file: {target_file.name}, size: {target_file.stat().st_size} bytes]",
                        "is_binary": True,
                    },
                    ensure_ascii=False,
                )

        ref_files, tmp_files, ast_files, scr_files = [], [], [], []
        if skill_dir:
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

        try:
            rel_path = str(skill_md.relative_to(SKILLS_DIR))
        except ValueError:
            rel_path = str(skill_md.relative_to(skill_md.parent.parent)) if skill_md.parent.parent else skill_md.name

        skill_name = parsed_frontmatter.get("name", skill_md.parent.name)
        req_envs = _get_required_environment_variables(parsed_frontmatter)
        backend = get_env_type()

        overrides = _env_overrides()
        rem_missing_envs = [e["name"] for e in req_envs if not e.get("optional") and not overrides.get(e["name"])]
        setup_needed = bool(rem_missing_envs)

        available_env_names = [e["name"] for e in req_envs if e["name"] not in rem_missing_envs]
        if available_env_names:
            try:
                register_env_passthrough(available_env_names)
            except Exception:
                logger.debug("Could not register env passthrough for skill %s", skill_name, exc_info=True)

        req_cred_files = parsed_frontmatter.get("required_credential_files")
        missing_cred_files = []
        if isinstance(req_cred_files, list) and req_cred_files:
            try:
                if missing_cred_files := [
                    rel_path
                    for entry in req_cred_files
                    if (
                        rel_path := (
                            entry.strip()
                            if isinstance(entry, str)
                            else (entry.get("path") or entry.get("name") or "").strip()
                            if isinstance(entry, dict)
                            else ""
                        )
                    )
                    if not register_credential_file(rel_path)
                ]:
                    setup_needed = True
            except Exception:
                logger.debug("Could not register credential files for skill %s", skill_name, exc_info=True)

        rendered = content

        result = {
            "success": True,
            "name": skill_name,
            "description": parsed_frontmatter.get("description", ""),
            "tags": tags,
            "related_skills": related_skills,
            "content": rendered,
            "path": rel_path,
            "skill_dir": str(skill_dir) if skill_dir else None,
            "linked_files": linked_files if linked_files else None,
            "usage_hint": (
                "To view linked files, call skill_view(name, file_path) where file_path is "
                "e.g. 'references/api.md' or 'assets/config.yaml'"
            )
            if linked_files
            else None,
            "required_environment_variables": req_envs,
            "required_commands": [],
            "missing_required_environment_variables": rem_missing_envs,
            "missing_credential_files": missing_cred_files,
            "missing_required_commands": [],
            "setup_needed": setup_needed,
            "setup_skipped": False,
            "readiness_status": SkillReadinessStatus.SETUP_NEEDED.value
            if setup_needed
            else SkillReadinessStatus.AVAILABLE.value,
        }

        if setup_help := next((e["help"] for e in req_envs if e.get("help")), None):
            result["setup_help"] = setup_help

        if setup_needed:
            missing_items = [f"env ${n}" for n in rem_missing_envs] + [f"file {p}" for p in missing_cred_files]
            if setup_note := _build_setup_note(SkillReadinessStatus.SETUP_NEEDED, missing_items, setup_help):
                if backend in _REMOTE_ENV_BACKENDS:
                    setup_note = (
                        f"{setup_note} {backend.upper()}-backed skills need these requirements "
                        "available inside the remote environment as well."
                    )
                result["setup_note"] = setup_note

        if parsed_frontmatter.get("compatibility"):
            result["compatibility"] = parsed_frontmatter["compatibility"]
        if isinstance(meta := parsed_frontmatter.get("metadata"), dict):
            result["metadata"] = meta

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        return tool_error(str(e), success=False)


SKILLS_LIST_SCHEMA = {
    "name": "skills_list",
    "description": "List available skills (name + description). Use skill_view(name) to load full content.",
    "parameters": {
        "type": "object",
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
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "The skill name (use skills_list to see available skills). "
                    "For plugin-provided skills, use the qualified form 'plugin:skill' "
                    "(e.g. 'superpowers:writing-plans')."
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

registry.register_tool("skills_list", schema=SKILLS_LIST_SCHEMA)(
    lambda args, **kw: skills_list(category=args.get("category"), task_id=kw.get("task_id")),
)


def _skill_view_handler(args: dict[str, Any], **kw: Any) -> str:
    # 廉价的 interrupt 提前返回：skill_view 从磁盘读取。
    # 没有这个兜底，过期 "please list skills" 调用会在用户已转移注意力后继续运行。
    if is_interrupted():
        return json.dumps({"error": "Interrupted", "interrupted": True})
    return skill_view(name=args.get("name", ""), file_path=args.get("file_path"), task_id=kw.get("task_id"))


registry.register_tool("skill_view", schema=SKILL_VIEW_SCHEMA)(_skill_view_handler)
