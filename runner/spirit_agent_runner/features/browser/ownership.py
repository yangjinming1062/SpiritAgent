from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spirit_agent_runner.features.browser.session import BrowserManager

_CURRENT_MANAGER: ContextVar["BrowserManager"] = ContextVar("browser_manager")


def current_manager() -> "BrowserManager":
    return _CURRENT_MANAGER.get()


@contextmanager
def browser_scope(manager: "BrowserManager") -> Iterator[None]:
    token = _CURRENT_MANAGER.set(manager)
    try:
        yield
    finally:
        _CURRENT_MANAGER.reset(token)
