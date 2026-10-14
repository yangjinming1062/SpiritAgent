import ast
import json
import tomllib
from collections.abc import Callable

import yaml

from spirit_agent_runner.features.files.models import _LINTER_UNUSABLE_PATTERNS


def _looks_like_linter_unusable(base_cmd: str, output: str) -> bool:
    """若 ``output`` 表明 ``base_cmd`` 自身无法运行则返回 True。"""
    patterns = _LINTER_UNUSABLE_PATTERNS.get(base_cmd)
    if not patterns:
        return False
    lower = output.lower()
    return any(p in lower for p in patterns)


def _lint_json_inproc(content: str) -> tuple[bool, str]:
    """进程内 JSON 语法校验。"""
    try:
        json.loads(content)
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"JSONDecodeError: {e.msg} (line {e.lineno}, column {e.colno})"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _lint_yaml_inproc(content: str) -> tuple[bool, str]:
    """进程内 YAML 语法校验。"""
    try:
        yaml.safe_load(content)
        return True, ""
    except yaml.YAMLError as e:
        return False, f"YAMLError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _lint_toml_inproc(content: str) -> tuple[bool, str]:
    """进程内 TOML 语法校验。"""
    try:
        tomllib.loads(content)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _lint_python_inproc(content: str) -> tuple[bool, str]:
    """通过 ast.parse 进行进程内 Python 语法校验。"""
    try:
        ast.parse(content)
        return True, ""
    except SyntaxError as e:
        loc = f" (line {e.lineno}, column {e.offset})" if e.lineno else ""
        return False, f"{type(e).__name__}: {e.msg}{loc}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


LINTERS_INPROC: dict[str, Callable[[str], tuple[bool, str]]] = {
    ".py": _lint_python_inproc,
    ".json": _lint_json_inproc,
    ".yaml": _lint_yaml_inproc,
    ".yml": _lint_yaml_inproc,
    ".toml": _lint_toml_inproc,
}
