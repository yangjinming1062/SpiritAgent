import threading
import uuid
from pathlib import Path
from typing import Any

from spirit_agent_runner.infrastructure.constants import get_spiritagent_home


def get_camofox_state_dir() -> Path:
    """返回 Camofox 受管持久化会话的状态目录。"""
    return get_spiritagent_home() / "browser_auth" / "camofox"


def get_camofox_identity(task_id: str | None = None) -> dict[str, str]:
    """按 (scope, task_id) 派生出确定性的 Camofox user_id 与 session_key。"""
    scope = str(get_camofox_state_dir())
    t_id = task_id or "default"
    return {
        "user_id": f"spiritagent_{uuid.uuid5(uuid.NAMESPACE_URL, f'camofox-user:{scope}').hex[:10]}",
        "session_key": f"task_{uuid.uuid5(uuid.NAMESPACE_URL, f'camofox-session:{scope}:{t_id}').hex[:16]}",
    }


class CamofoxState:
    """In-memory REST identities and VNC discovery cache, owned by BrowserManager."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.vnc_urls: dict[str, str | None] = {}

    def close(self) -> None:
        # Remote managed identities persist across Runner restarts.
        with self.lock:
            self.sessions.clear()
            self.vnc_urls.clear()
