from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from .constants import get_skills_dir
from .credential_files import get_external_skills_dirs

_PRESETS = frozenset({"companion", "developer", "product_manager", "copywriter", "language_teacher"})


@dataclass(frozen=True, slots=True)
class SkillScope:
    user_id: int
    system_preset_id: str

    @classmethod
    def parse(cls, raw: object) -> "SkillScope":
        if (
            not isinstance(raw, dict)
            or type(raw.get("user_id")) is not int
            or raw["user_id"] <= 0
            or raw.get("system_preset_id") not in _PRESETS
        ):
            raise ValueError("Valid server-controlled skill scope is required")
        return cls(raw["user_id"], raw["system_preset_id"])


CURRENT_SKILL_SCOPE: ContextVar[SkillScope | None] = ContextVar("skill_scope", default=None)


def learned_skills_root() -> Path:
    scope = CURRENT_SKILL_SCOPE.get()
    if scope is None:
        raise ValueError("Learning skills requires a scoped invocation")
    private = get_skills_dir().parent / "learned-skills"
    root = private / str(scope.user_id) / scope.system_preset_id
    if root.resolve() != private.resolve() / str(scope.user_id) / scope.system_preset_id:
        raise ValueError("Learned skill scope cannot be redirected")
    return root


def visible_skill_roots() -> list[Path]:
    roots = [learned_skills_root()] if CURRENT_SKILL_SCOPE.get() is not None else []
    private_root = (get_skills_dir().parent / "learned-skills").resolve()
    for root in [get_skills_dir(), *get_external_skills_dirs()]:
        if not root.resolve().is_relative_to(private_root):
            roots.append(root)
    return roots


def visible_skill_path(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        return False
    private_root = (get_skills_dir().parent / "learned-skills").resolve()
    return not resolved.is_relative_to(private_root) or (
        CURRENT_SKILL_SCOPE.get() is not None and resolved.is_relative_to(learned_skills_root().resolve())
    )
