import ast
import json
import tomllib
from collections.abc import Callable

import yaml

# 外部检查器命令：``{file}`` 为占位符；进程内能校验的格式见 LINTERS_INPROC。
LINTERS: dict[str, str] = {
    ".js": "node --check {file} 2>&1",
    # --no-install：防 npx 临时下同名包。
    ".ts": "npx --no-install tsc --noEmit {file} 2>&1",
    ".go": "go vet {file} 2>&1",
    ".rs": "rustfmt --check {file} 2>&1",
}

# 命令输出命中任一模式时视为检查器自身不可用（环境缺依赖），按 skipped 处理而非报语法错误。
_LINTER_UNUSABLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "npx": (
        "this is not the tsc command you are looking for",
        "could not determine executable to run",
        "not found in npm registry",
        "canceled due to missing packages",
    ),
    "rustfmt": ("no input filename given", "error: not a workspace"),
    "go": ("cannot find package", "go: cannot find main module"),
}


def _looks_like_linter_unusable(base_cmd: str, output: str) -> bool:
    patterns = _LINTER_UNUSABLE_PATTERNS.get(base_cmd)
    return bool(patterns) and any(p in output.lower() for p in patterns)


def _lint_json_inproc(content: str) -> tuple[bool, str]:
    try:
        json.loads(content)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _lint_yaml_inproc(content: str) -> tuple[bool, str]:
    try:
        yaml.safe_load(content)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _lint_toml_inproc(content: str) -> tuple[bool, str]:
    try:
        tomllib.loads(content)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _lint_python_inproc(content: str) -> tuple[bool, str]:
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
