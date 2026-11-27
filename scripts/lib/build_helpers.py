#!/usr/bin/env python3
"""客户端构建的版本同步、载荷暂存和桌面产物定位。"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def get_repo_root() -> Path:
    return REPO_ROOT


def set_version(version: str, repo_root: Path | None = None) -> None:
    """写入版本号至 client/installer package.json、tauri.conf.json、Cargo.toml 与 runner/pyproject.toml。"""
    root = repo_root or get_repo_root()
    print(f"==> [build_helpers] Writing version {version} to package.json/pyproject.toml")

    json_files = [
        root / "client" / "package.json",
        root / "installer" / "package.json",
        root / "installer" / "src-tauri" / "tauri.conf.json",
    ]
    for p in json_files:
        if not p.is_file():
            raise FileNotFoundError(f"Missing expected json file: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        data["version"] = version
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    for p in (root / "installer" / "src-tauri" / "Cargo.toml", root / "runner" / "pyproject.toml"):
        text = p.read_text(encoding="utf-8")
        text = re.sub(r'^version = "[^"]+"', f'version = "{version}"', text, count=1, flags=re.MULTILINE)
        p.write_text(text, encoding="utf-8", newline="\n")


def _read_runner_pyproject_version(root: Path) -> str:
    pyproject = root / "runner" / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"cannot read version from {pyproject}")
    return match.group(1)


def _select_runner_wheel(dist_dir: Path, version: str) -> Path:
    """暂存产物必须是与 pyproject 版本精确匹配的唯一 wheel。"""
    exact = sorted(dist_dir.glob(f"spirit_agent-{version}-*.whl"))
    if not exact:
        available = sorted(p.name for p in dist_dir.glob("*.whl"))
        raise FileNotFoundError(
            f"runner wheel for version {version} not found in {dist_dir}. "
            f"Available: {available or 'none'}. Rebuild the runner wheel for this version "
            f"(`uv build --wheel` in runner/) before staging — do not reuse an old wheel.",
        )
    if len(exact) > 1:
        raise RuntimeError(f"multiple wheels for version {version} in {dist_dir}: {[p.name for p in exact]}")
    return exact[0]


def stage_payload(repo_root: Path | None = None) -> None:
    """暂存 payload 至 installer/payload/（Runner wheel、技能与安装脚本）。"""
    root = repo_root or get_repo_root()
    print("==> [build_helpers] Staging payload in installer/payload/")

    installer = root / "installer"
    payload = installer / "payload"
    payload_runner = payload / "runner"
    payload_client = payload / "client"

    if payload_runner.exists():
        shutil.rmtree(payload_runner)
    if payload_client.exists():
        shutil.rmtree(payload_client)

    payload_runner.mkdir(parents=True, exist_ok=True)
    payload_client.mkdir(parents=True, exist_ok=True)

    version = _read_runner_pyproject_version(root)
    dist_dir = root / "runner" / "dist"
    wheel = _select_runner_wheel(dist_dir, version)
    shutil.copy2(wheel, payload_runner / wheel.name)

    # 对实际交付的 wheel 隔离安装，验证包内入口、依赖和静态工具定义。
    subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "check_runner_package.py"),
            "--wheel",
            str(payload_runner / wheel.name),
        ],
        check=True,
        cwd=root,
    )

    # 内嵌 ZIP 只收录普通文件和目录。
    skills_dst = payload / "skills"
    if skills_dst.is_symlink() or skills_dst.is_file():
        skills_dst.unlink()
    elif skills_dst.is_dir():
        shutil.rmtree(skills_dst)
    shutil.copytree(installer / "skills", skills_dst)

    for name in ("install.sh", "install.ps1"):
        target = payload / name
        target.unlink(missing_ok=True)
        shutil.copy2(installer / name, target)

    wheel_size = (payload_runner / wheel.name).stat().st_size
    print(f"    runner wheel: {wheel.name} ({wheel_size} bytes)")
    print("    install scripts: install.sh, install.ps1")


def find_desktop_artifact(target: str, version: str, repo_root: Path | None = None) -> Path | None:
    """在 client/release/ 中定位桌面产物。"""
    root = repo_root or get_repo_root()
    arch = subprocess.check_output(["node", "-p", "process.arch"], text=True).strip()
    extension = {"mac": "dmg", "win": "exe"}[target]
    path = root / "client" / "release" / f"SpiritAgent-{version}-{target}-{arch}.{extension}"
    return path if path.is_file() else None
