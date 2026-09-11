import re
import shutil
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

_SESSION_ID_RE = re.compile(r"^\d{1,20}$")


def _validate_session_id(session_id: str) -> str:
    """只接受纯数字 id，与 ``Conversation.id`` 类型一致。"""
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"invalid session_id format: {session_id!r}")
    return session_id


def attachment_root(data_dir: str) -> Path:
    return Path(data_dir) / "desktop-attachments"


def session_dir(data_dir: str, session_id: str) -> Path:
    safe_id = _validate_session_id(session_id)
    return attachment_root(data_dir) / safe_id


def path_attach_ref(path: str) -> dict:
    """Path 模式 attach 信封；path 在 Docker 模式下后端无法 stat，视为不透明；ref_text 供渲染端提示词替换。"""
    normalized_path = path.replace("\\", "/")
    return {"attached": True, "path": path, "ref_text": f"@file:{normalized_path}", "size": 0}


def gc_session(data_dir: str, session_id: str) -> None:
    safe_id = _validate_session_id(session_id)
    root = attachment_root(data_dir).resolve()
    target = session_dir(data_dir, safe_id).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"gc_session path escapes root: {target}")
    if target.exists():
        shutil.rmtree(target)
        logger.info("gc_session removed", extra={"target": str(target)})
