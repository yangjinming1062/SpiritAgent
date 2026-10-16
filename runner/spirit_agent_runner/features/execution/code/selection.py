"""脚本的解释器、工作目录和环境选择。"""

import functools
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from typing import Any

from spirit_agent_runner.contracts.config import cfg_get, cfg_str, load_config
from spirit_agent_runner.features.execution.api import current_task_id, use_environment
from spirit_agent_runner.infrastructure.constants import CREATE_NO_WINDOW, IS_WINDOWS, get_subprocess_home
from spirit_agent_runner.infrastructure.env_passthrough import is_env_passthrough
from spirit_agent_runner.infrastructure.path_helpers import find_python

logger = logging.getLogger(__name__)


EXECUTION_MODES = ("project", "strict")


DEFAULT_EXECUTION_MODE = "project"


DEFAULT_TIMEOUT = 300


DEFAULT_MAX_TOOL_CALLS = 50


_SAFE_ENV_PREFIXES = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SHELL",
    "LOGNAME",
    "XDG_",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CONDA",
)


_SECRET_SUBSTRINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "PASSWD", "AUTH", "DSN", "WEBHOOK")


SPIRITAGENT_CHILD_ALLOWED = frozenset({"SPIRITAGENT_HOME"})


_WINDOWS_ESSENTIAL_ENV_VARS = frozenset(
    {
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "OS",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        "PUBLIC",
        "ALLUSERSPROFILE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "USERDOMAIN",
        "USERNAME",
        "HOMEDRIVE",
        "HOMEPATH",
        "COMPUTERNAME",
    },
)


def _scrub_child_env(source_env: Mapping[str, str]) -> dict[str, str]:
    scrubbed: dict[str, str] = {}
    _dropped_spiritagent = []
    for k, v in source_env.items():
        if is_env_passthrough(k, config=load_config()):
            scrubbed[k] = v
            continue
        if any(s in k.upper() for s in _SECRET_SUBSTRINGS):
            continue
        if any(k.startswith(p) for p in _SAFE_ENV_PREFIXES):
            scrubbed[k] = v
            continue
        if k in SPIRITAGENT_CHILD_ALLOWED:
            scrubbed[k] = v
            continue
        if IS_WINDOWS and k.upper() in _WINDOWS_ESSENTIAL_ENV_VARS:
            scrubbed[k] = v
            continue
        if k.startswith("SPIRITAGENT_"):
            _dropped_spiritagent.append(k)
    if _dropped_spiritagent:
        logger.debug(
            "execute_code: dropped %d non-allowlisted SPIRITAGENT_* var(s) from the script child env (%s); "
            "declare them in env_passthrough to pass them through.",
            len(_dropped_spiritagent),
            ", ".join(sorted(_dropped_spiritagent)),
        )
    return scrubbed


def build_child_env(staging_dir: str, rpc_endpoint: str, rpc_token: str) -> dict[str, str]:
    child_env = _scrub_child_env(os.environ)
    child_env["SPIRITAGENT_RPC_SOCKET"] = rpc_endpoint
    child_env["SPIRITAGENT_RPC_TOKEN"] = rpc_token
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONPATH"] = os.pathsep.join(p for p in (staging_dir, child_env.get("PYTHONPATH", "")) if p)
    child_env["HOME"] = str(get_subprocess_home())
    if tz := str(cfg_get(load_config(), "terminal", "timezone", default="")).strip():
        child_env["TZ"] = tz
    return child_env


def code_execution_config() -> dict[str, Any]:
    cfg = load_config().get("code_execution")
    return cfg if isinstance(cfg, dict) else {}


def execution_mode(cfg: dict[str, Any]) -> str:
    """读取合法的 ``code_execution.mode``, 非法值降级为默认。"""
    mode = cfg_str(cfg, "mode", DEFAULT_EXECUTION_MODE).lower()
    if mode in EXECUTION_MODES:
        return mode
    logger.warning(
        "Ignoring code_execution.mode=%r (expected one of %s), falling back to %r",
        mode,
        EXECUTION_MODES,
        DEFAULT_EXECUTION_MODE,
    )
    return DEFAULT_EXECUTION_MODE


@functools.lru_cache(maxsize=32)
def _is_usable_python(python_path: str) -> bool:
    """探测解释器能否运行且版本 >= 3.8。"""
    try:
        result = subprocess.run(
            [python_path, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"],
            timeout=5,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_child_python(mode: str) -> str:
    """strict 使用 Runner Python；project 依次查用户指定或托管 Python、激活的虚拟环境、Runner Python。"""
    if mode != "project":
        return sys.executable
    if (managed := find_python()) and _is_usable_python(managed):
        return managed
    exe_names, subdir = (("python.exe", "python3.exe"), "Scripts") if IS_WINDOWS else (("python", "python3"), "bin")
    for var in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        if not (root := os.environ.get(var, "").strip()):
            continue
        for exe in exe_names:
            candidate = os.path.join(root, subdir, exe)
            if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
                continue
            if _is_usable_python(candidate):
                return candidate
            logger.info(
                "execute_code: skipping %s=%s (Python version < 3.8 or broken). Using sys.executable instead.",
                var,
                candidate,
            )
            return sys.executable
    return sys.executable


def resolve_child_cwd(mode: str, staging_dir: str) -> str:
    """project 模式在终端当前工作目录运行（随终端 cd 变化，目录不存在时回落 staging），strict 模式直接用 staging。"""
    if mode != "project":
        return staging_dir
    with use_environment(current_task_id()) as env:
        cwd = env.cwd
    return cwd if os.path.isdir(cwd) else staging_dir
