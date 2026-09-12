#!/usr/bin/env python3
"""构建期门禁：刚打出的 runner wheel 必须满足 `server.py` 的本地包导入。

质量门放在发布前，而不是安装/更新时回滚。wheel 与 server.py 不一致（例如
`from utils import CURRENT_SKILL_SCOPE` 而 wheel 里没有 `utils/memory_scope`）
应在 `uv build` 之后、staging/安装器打包之前直接失败。

用法：
    python scripts/check_runner_facade.py
    python scripts/check_runner_facade.py --wheel runner/dist/spirit_agent-1.1.0-py3-none-any.whl
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 在隔离 venv 中执行：把 wheel 装进去，再对 staged/仓库 server.py 做导入面校验。
_FACADE_CHECK = r"""
import ast, importlib, importlib.util, sys
from pathlib import Path

server_py = Path(sys.argv[1])
tree = ast.parse(server_py.read_text(encoding="utf-8"))
local_roots = {"envs", "runner_version", "tools", "utils"}
missing = []


def ensure_spec(name: str) -> None:
    try:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    except ImportError:
        missing.append(name)


for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in local_roots:
                ensure_spec(alias.name)
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        root = node.module.split(".")[0]
        if root not in local_roots:
            continue
        try:
            mod = importlib.import_module(node.module)
        except ImportError:
            missing.append(node.module)
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            if hasattr(mod, alias.name):
                continue
            ensure_spec(f"{node.module}.{alias.name}")

if missing:
    raise SystemExit("wheel does not satisfy server.py imports: " + ", ".join(sorted(set(missing))))
print("runner wheel facade ok")
"""


def find_latest_wheel(runner_dir: Path) -> Path:
    dist = runner_dir / "dist"
    wheels = sorted(dist.glob("spirit_agent-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise SystemExit(f"no spirit_agent wheel found in {dist} (run uv build first)")
    return wheels[-1]


def check(wheel: Path, server_py: Path) -> None:
    if not wheel.is_file():
        raise SystemExit(f"wheel not found: {wheel}")
    if not server_py.is_file():
        raise SystemExit(f"server.py not found: {server_py}")

    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv not found in PATH")

    with tempfile.TemporaryDirectory(prefix="spiritagent-facade-") as tmp:
        tmp_path = Path(tmp)
        venv_dir = tmp_path / "venv"
        print(f"==> check_runner_facade: {wheel.name} vs {server_py}")
        subprocess.run([uv, "venv", str(venv_dir)], check=True)
        python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), str(wheel)],
            check=True,
        )
        check_py = tmp_path / "facade_check.py"
        check_py.write_text(_FACADE_CHECK, encoding="utf-8")
        subprocess.run([str(python), str(check_py), str(server_py)], check=True)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    runner_dir = repo_root / "runner"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="path to built wheel (default: newest in runner/dist)",
    )
    parser.add_argument(
        "--server-py",
        type=Path,
        default=runner_dir / "server.py",
        help="server.py to validate against the wheel",
    )
    args = parser.parse_args()
    wheel = args.wheel.resolve() if args.wheel else find_latest_wheel(runner_dir)
    check(wheel, args.server_py.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
