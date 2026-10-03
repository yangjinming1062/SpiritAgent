import re
import shutil
from pathlib import Path

from .config import SETTINGS
from .logger import get_logger

logger = get_logger(__name__)

_SESSION_ID_RE = re.compile(r"[0-9]{1,20}")


def _validate_session_id(session_id: str) -> str:
    """只接受纯数字 id，与 ``Conversation.id`` 类型一致。"""
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise ValueError(f"invalid session_id format: {session_id!r}")
    return session_id


def attachment_root() -> Path:
    return Path(SETTINGS.data_dir) / "desktop-attachments"


def session_dir(session_id: str) -> Path:
    return attachment_root() / _validate_session_id(session_id)


def gc_session(session_id: str) -> None:
    root = attachment_root().resolve()
    target = session_dir(session_id).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"gc_session path escapes root: {target}")
    if target.exists():
        shutil.rmtree(target)
        logger.info("gc_session removed", extra={"target": str(target)})
