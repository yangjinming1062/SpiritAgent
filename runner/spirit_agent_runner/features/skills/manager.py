import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class SkillManager:
    def __init__(self) -> None:
        self.mutation_lock = threading.RLock()


_CURRENT: ContextVar[SkillManager] = ContextVar("skill_manager")


def current_manager() -> SkillManager:
    return _CURRENT.get()


@contextmanager
def skill_scope(manager: SkillManager) -> Iterator[None]:
    token = _CURRENT.set(manager)
    try:
        yield
    finally:
        _CURRENT.reset(token)
