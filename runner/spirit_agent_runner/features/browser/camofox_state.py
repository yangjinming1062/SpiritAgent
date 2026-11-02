import threading
import uuid
from pathlib import Path
from typing import Any

from spirit_agent_runner.infrastructure.constants import get_spiritagent_home


def get_camofox_state_dir() -> Path:
    """返回 Camofox 受管持久化会话的状态目录。"""
    return get_spiritagent_home() / "browser_auth" / "camofox"


def get_camofox_identity() -> dict[str, str]:
    """按状态目录 scope 派生出确定性的 Camofox user_id 与 session_key。"""
    scope = str(get_camofox_state_dir())
    return {
        "user_id": f"spiritagent_{uuid.uuid5(uuid.NAMESPACE_URL, f'camofox-user:{scope}').hex[:10]}",
        "session_key": f"task_{uuid.uuid5(uuid.NAMESPACE_URL, f'camofox-session:{scope}').hex[:16]}",
    }


class CamofoxState:
    """In-memory REST identity and VNC discovery cache, owned by BrowserManager."""

    def __init__(self) -> None:
        self.session: dict[str, Any] | None = None
        self.lock = threading.Lock()
        self.vnc_urls: dict[str, str | None] = {}

    def close(self) -> None:
        # 受管身份在远端持久化，Runner 重启后仍可复用；本地缓存直接丢弃。
        with self.lock:
            self.session = None
            self.vnc_urls.clear()
