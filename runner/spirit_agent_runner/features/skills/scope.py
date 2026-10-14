from pathlib import Path

from spirit_agent_runner.contracts.config import load_config
from spirit_agent_runner.contracts.context import current_context
from spirit_agent_runner.infrastructure.constants import get_skills_dir
from spirit_agent_runner.infrastructure.credential_files import get_external_skills_dirs


def learned_skills_root() -> Path:
    scope = current_context().skill_scope
    if scope is None:
        raise ValueError("Learning skills requires a scoped invocation")
    private = get_skills_dir().parent / "learned-skills"
    root = private / str(scope.user_id) / scope.system_preset_id
    if root.resolve() != private.resolve() / str(scope.user_id) / scope.system_preset_id:
        raise ValueError("Learned skill scope cannot be redirected")
    return root


def visible_skill_roots() -> list[Path]:
    roots = [learned_skills_root()] if current_context().skill_scope is not None else []
    private_root = (get_skills_dir().parent / "learned-skills").resolve()
    for root in [get_skills_dir(), *get_external_skills_dirs(config=load_config())]:
        if not root.resolve().is_relative_to(private_root):
            roots.append(root)
    return roots


def visible_skill_path(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        return False
    private_root = (get_skills_dir().parent / "learned-skills").resolve()
    return not resolved.is_relative_to(private_root) or (
        current_context().skill_scope is not None and resolved.is_relative_to(learned_skills_root().resolve())
    )
