#!/usr/bin/env python3
"""scripts/lib/build_helpers.py —— 客户端构建管线的跨平台共享助手。

统一处理：
- Set-Version：同步 5 个包管理与配置文件版本号
- Stage-Payload：暂存 runner wheel、server.py、skills 与 install 脚本
- Patch-TauriConfig / Restore-TauriConfig：安全补丁与恢复 installer/src-tauri/tauri.conf.json 中的 bundle.resources
- Find-DesktopArtifact：定位 electron-builder 产物
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def get_repo_root() -> Path:
    return REPO_ROOT


def set_version(version: str, repo_root: Path | None = None) -> None:
    """写入版本号至 client/package.json, installer/package.json,
    installer/src-tauri/tauri.conf.json, installer/src-tauri/Cargo.toml, runner/pyproject.toml。
    """
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

    cargo_toml = root / "installer" / "src-tauri" / "Cargo.toml"
    if cargo_toml.is_file():
        text = cargo_toml.read_text(encoding="utf-8")
        text = re.sub(r'^version = "[^"]+"', f'version = "{version}"', text, count=1, flags=re.MULTILINE)
        cargo_toml.write_text(text, encoding="utf-8", newline="\n")
    else:
        raise FileNotFoundError(f"Missing Cargo.toml: {cargo_toml}")

    runner_pyproject = root / "runner" / "pyproject.toml"
    if runner_pyproject.is_file():
        text = runner_pyproject.read_text(encoding="utf-8")
        text = re.sub(r'^version = "[^"]+"', f'version = "{version}"', text, count=1, flags=re.MULTILINE)
        runner_pyproject.write_text(text, encoding="utf-8", newline="\n")
    else:
        raise FileNotFoundError(f"Missing runner/pyproject.toml: {runner_pyproject}")


def _link_or_copy(src: Path, dst: Path, is_dir: bool = False) -> None:
    """跨平台创建软链/硬链/Junction，失败时回退为拷贝。"""
    if dst.is_symlink() or dst.is_file():
        dst.unlink(missing_ok=True)
    elif dst.is_dir():
        shutil.rmtree(dst)

    if sys.platform == "win32":
        if is_dir:
            cmd = f'mklink /J "{dst}" "{src}"'
            ret = subprocess.run(["cmd", "/c", cmd], capture_output=True, text=True, check=False)
            if ret.returncode != 0:
                shutil.copytree(src, dst)
        else:
            cmd = f'mklink /H "{dst}" "{src}"'
            ret = subprocess.run(["cmd", "/c", cmd], capture_output=True, text=True, check=False)
            if ret.returncode != 0:
                shutil.copy2(src, dst)
    else:
        try:
            rel_target = os.path.relpath(src, dst.parent)
            dst.symlink_to(rel_target, target_is_directory=is_dir)
        except OSError:
            if is_dir:
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)


def stage_payload(repo_root: Path | None = None, target: str | None = None) -> None:
    """暂存 payload 至 installer/payload/ (runner wheel, server.py, skills, install 脚本)。"""
    root = repo_root or get_repo_root()
    print("==> [build_helpers] Staging payload in installer/payload/")

    payload_runner = root / "installer" / "payload" / "runner"
    payload_client = root / "installer" / "payload" / "client"

    if payload_runner.exists():
        shutil.rmtree(payload_runner)
    if payload_client.exists():
        shutil.rmtree(payload_client)

    payload_runner.mkdir(parents=True, exist_ok=True)
    payload_client.mkdir(parents=True, exist_ok=True)

    dist_dir = root / "runner" / "dist"
    wheels = sorted(dist_dir.glob("spirit*-agent-*.whl"))
    if not wheels:
        wheels = sorted(dist_dir.glob("*.whl"))
    if not wheels:
        raise FileNotFoundError(f"No wheel found in {dist_dir} (build runner first)")

    wheel = wheels[0]
    shutil.copy2(wheel, payload_runner / wheel.name)

    server_py = root / "runner" / "server.py"
    if not server_py.is_file():
        raise FileNotFoundError(f"runner/server.py not found: {server_py}")
    shutil.copy2(server_py, payload_runner / "server.py")

    skills_src = root / "installer" / "skills"
    skills_dst = root / "installer" / "payload" / "skills"
    if skills_src.exists():
        _link_or_copy(skills_src, skills_dst, is_dir=True)

    install_sh = root / "installer" / "install.sh"
    if install_sh.is_file():
        _link_or_copy(install_sh, root / "installer" / "payload" / "install.sh", is_dir=False)

    install_ps1 = root / "installer" / "install.ps1"
    if install_ps1.is_file():
        _link_or_copy(install_ps1, root / "installer" / "payload" / "install.ps1", is_dir=False)

    wheel_size = (payload_runner / wheel.name).stat().st_size
    print(f"    runner wheel: {wheel.name} ({wheel_size} bytes)")
    print("    install scripts: install.sh, install.ps1")


def get_tauri_conf_path(repo_root: Path | None = None) -> Path:
    root = repo_root or get_repo_root()
    return root / "installer" / "src-tauri" / "tauri.conf.json"


def patch_tauri_config(repo_root: Path | None = None) -> None:
    """备份并修改 installer/src-tauri/tauri.conf.json，将 payload/client 中的桌面产物追加至 bundle.resources。"""
    root = repo_root or get_repo_root()
    conf_path = get_tauri_conf_path(root)
    bak_path = conf_path.with_name(conf_path.name + ".build_client.bak")

    if not conf_path.is_file():
        raise FileNotFoundError(f"tauri.conf.json not found: {conf_path}")

    # 制作备份
    shutil.copy2(conf_path, bak_path)

    client_payload_dir = root / "installer" / "payload" / "client"
    client_files = [f for f in client_payload_dir.iterdir() if f.is_file()]
    if not client_files:
        raise FileNotFoundError(f"No desktop artifact in {client_payload_dir}")

    desktop_name = client_files[0].name
    desktop_rel = f"../payload/client/{desktop_name}"

    print(f"==> [build_helpers] Patching {conf_path}: bundle.resources += {desktop_rel}")
    data = json.loads(conf_path.read_text(encoding="utf-8"))

    bundle = data.setdefault("bundle", {})
    resources: list[str] = bundle.setdefault("resources", [])
    if desktop_rel not in resources:
        resources.append(desktop_rel)

    conf_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def restore_tauri_config(repo_root: Path | None = None) -> None:
    """恢复 installer/src-tauri/tauri.conf.json 备份并删除备份文件。"""
    root = repo_root or get_repo_root()
    conf_path = get_tauri_conf_path(root)
    bak_path = conf_path.with_name(conf_path.name + ".build_client.bak")

    if bak_path.is_file():
        print(f"==> [build_helpers] Restoring {conf_path}")
        shutil.move(str(bak_path), str(conf_path))


def find_desktop_artifact(target: str, version: str, repo_root: Path | None = None) -> Path | None:
    """在 client/release/ 中定位 desktop artifact。"""
    root = repo_root or get_repo_root()
    release_dir = root / "client" / "release"
    if not release_dir.is_dir():
        return None

    if target == "mac":
        pattern = f"SpiritAgent-{version}-mac-*.dmg"
    elif target == "win":
        pattern = f"SpiritAgent-{version}-win-*.exe"
    else:
        pattern = f"SpiritAgent-{version}-*"

    matches = sorted(release_dir.glob(pattern))
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser(description="SpiritAgent build helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # set-version
    p_ver = subparsers.add_parser("set-version", help="Update version in all project config files")
    p_ver.add_argument("version", help="Semantic version string, e.g. 0.16.0")

    # stage-payload
    p_stage = subparsers.add_parser("stage-payload", help="Stage wheel, server.py, skills into installer/payload/")
    p_stage.add_argument("--target", choices=["mac", "win"], default=None)

    # patch-tauri-config
    subparsers.add_parser("patch-tauri-config", help="Patch tauri.conf.json bundle.resources")

    # restore-tauri-config
    subparsers.add_parser("restore-tauri-config", help="Restore tauri.conf.json from backup")

    # find-desktop-artifact
    p_find = subparsers.add_parser("find-desktop-artifact", help="Find desktop artifact path in client/release")
    p_find.add_argument("--target", choices=["mac", "win"], required=True)
    p_find.add_argument("--version", required=True)

    args = parser.parse_args()

    try:
        if args.command == "set-version":
            set_version(args.version)
        elif args.command == "stage-payload":
            stage_payload(target=args.target)
        elif args.command == "patch-tauri-config":
            patch_tauri_config()
        elif args.command == "restore-tauri-config":
            restore_tauri_config()
        elif args.command == "find-desktop-artifact":
            artifact = find_desktop_artifact(args.target, args.version)
            if artifact:
                print(str(artifact))
            else:
                print(f"error: artifact not found for target={args.target}, version={args.version}", file=sys.stderr)
                return 1
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
