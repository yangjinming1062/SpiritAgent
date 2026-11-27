#!/usr/bin/env python3
"""SpiritAgent 跨平台客户端构建总入口（流程与产物说明见 scripts/README.md）。"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from build_helpers import (  # noqa: E402
    find_desktop_artifact,
    get_repo_root,
    set_version,
    stage_payload,
)


def run_cmd(cmd: list[str], cwd: Path | None = None) -> None:
    display_cmd = " ".join(cmd)
    print(f"==> [exec] {display_cmd} (in {cwd or '.'})")
    if os.name == "nt":
        # Windows CreateProcess 不按 PATHEXT 解析；经 which 落到完整路径，否则 pnpm 等 .cmd shim 会 WinError 2。
        resolved = shutil.which(cmd[0])
        if resolved:
            cmd = [resolved, *cmd[1:]]
    ret = subprocess.run(cmd, cwd=cwd, check=False)
    if ret.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {ret.returncode}: {display_cmd}")


def check_required_tools(tools: list[str]) -> None:
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        raise RuntimeError(f"Required build dependencies not found in PATH: {', '.join(missing)}")


def build_runner(repo_root: Path) -> None:
    print("==> Building runner (uv build wheel → dist/spirit_agent-*.whl)")
    runner_dir = repo_root / "runner"
    # setuptools 会复用 build/lib；使用独立源码副本，确保 wheel 仅包含当前包。
    with tempfile.TemporaryDirectory(prefix="spiritagent-runner-build-") as tmp:
        source = Path(tmp)
        shutil.copy2(runner_dir / "pyproject.toml", source / "pyproject.toml")
        shutil.copytree(
            runner_dir / "spirit_agent_runner",
            source / "spirit_agent_runner",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        run_cmd(["uv", "build", "--wheel", "--out-dir", str(runner_dir / "dist")], cwd=source)
    # 隔离安装门禁由 stage_payload 对实际入包的 wheel 执行。


def build_desktop(repo_root: Path, target: str) -> None:
    print(f"==> Building client for target: {target}")
    client_dir = repo_root / "client"
    run_cmd(["pnpm", "install", "--frozen-lockfile"], cwd=client_dir)

    # typecheck 已由 dist:* 的 build 链内含（两遍 tsc --noEmit），client 无独立测试脚本。
    pnpm_target = "dist:mac:dmg" if target == "mac" else "dist:win:nsis"
    run_cmd(["pnpm", "run", pnpm_target], cwd=client_dir)


def find_installer_dmg(installer_dir: Path, version: str) -> Path:
    tauri_dir = installer_dir / "src-tauri"
    config = json.loads((tauri_dir / "tauri.conf.json").read_text(encoding="utf-8"))
    bundle_dir = tauri_dir / "target" / "release" / "bundle" / "dmg"
    prefix = f"{config['productName']}_{version}_"
    matches = [p for p in bundle_dir.glob("*.dmg") if p.is_file() and p.name.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {prefix}*.dmg in {bundle_dir}, found {len(matches)}")
    return matches[0]


def main() -> int:
    # Windows 控制台默认非 UTF-8，print 非 ASCII 会崩；统一 UTF-8 并降级替换。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # runner 的 cua-driver wheel 约 38 MB，CI 与弱网下会超过 uv 默认 30s 读超时；显式设置的环境变量优先。
    os.environ.setdefault("UV_HTTP_TIMEOUT", "300")

    parser = argparse.ArgumentParser(description="Build SpiritAgent client installer")
    parser.add_argument("--version", required=True, help="Release version (e.g. 0.16.0)")
    parser.add_argument("--target", choices=["mac", "win"], default=None, help="Target OS (default: infer from host)")
    parser.add_argument("--skip-runner", action="store_true", help="Skip runner build")
    parser.add_argument("--skip-desktop", action="store_true", help="Skip desktop build")
    parser.add_argument("--sign-identity", default=None, help="macOS codesign identity")
    parser.add_argument("--notary-profile", default=None, help="macOS notarytool profile name")
    parser.add_argument("--sign-tool", default="signtool.exe", help="Windows signtool executable path")
    parser.add_argument("--cert-thumbprint", default=None, help="Windows certificate SHA-1 thumbprint")
    parser.add_argument("--output", default=None, help="Output directory (default: <repo>/release)")

    args = parser.parse_args()
    repo_root = get_repo_root()
    output_dir = Path(args.output).resolve() if args.output else repo_root / "release"

    host_system = platform.system()
    target = args.target
    if not target:
        if host_system == "Darwin":
            target = "mac"
        elif host_system == "Windows":
            target = "win"
        else:
            print(f"error: unsupported host OS '{host_system}'. Pass --target.", file=sys.stderr)
            return 1

    if target == "mac" and host_system != "Darwin":
        print(f"error: --target mac requires a macOS host (got '{host_system}')", file=sys.stderr)
        return 1
    if target == "win" and host_system != "Windows":
        print(f"error: --target win requires a Windows host (got '{host_system}')", file=sys.stderr)
        return 1

    check_required_tools(["uv", "pnpm", "node"])
    if target == "mac":
        check_required_tools(["hdiutil", "codesign"])

    set_version(args.version, repo_root)

    if not args.skip_runner:
        build_runner(repo_root)
    else:
        print("==> Skipping runner build (--skip-runner)")

    if not args.skip_desktop:
        build_desktop(repo_root, target)
    else:
        print("==> Skipping desktop build (--skip-desktop)")

    desktop_artifact = find_desktop_artifact(target, args.version, repo_root)
    if not desktop_artifact or not desktop_artifact.is_file():
        print(
            f"error: desktop artifact for target={target} version={args.version} not found in client/release/",
            file=sys.stderr,
        )
        return 1
    print(f"==> Desktop artifact: {desktop_artifact}")

    stage_payload(repo_root)
    dest_desktop_path = repo_root / "installer" / "payload" / "client" / desktop_artifact.name

    if target == "mac" and args.sign_identity:
        print(f"==> Code-signing {desktop_artifact.name}")
        run_cmd(
            [
                "codesign",
                "--deep",
                "--force",
                "--options",
                "runtime",
                "--sign",
                args.sign_identity,
                str(desktop_artifact),
            ],
        )
        if args.notary_profile:
            print(f"==> Notarizing {desktop_artifact.name}")
            run_cmd(
                [
                    "xcrun",
                    "notarytool",
                    "submit",
                    str(desktop_artifact),
                    "--keychain-profile",
                    args.notary_profile,
                    "--wait",
                ],
            )
            run_cmd(["xcrun", "stapler", "staple", str(desktop_artifact)])
    elif target == "win" and args.cert_thumbprint:
        print(f"==> Code-signing {desktop_artifact.name}")
        run_cmd(
            [
                args.sign_tool,
                "sign",
                "/tr",
                "http://timestamp.digicert.com",
                "/td",
                "sha256",
                "/fd",
                "sha256",
                "/a",
                "/sha1",
                args.cert_thumbprint,
                str(desktop_artifact),
            ],
        )
    shutil.copy2(desktop_artifact, dest_desktop_path)

    installer_dir = repo_root / "installer"
    print("==> Tauri build")
    run_cmd(["pnpm", "install", "--frozen-lockfile"], cwd=installer_dir)
    if target == "mac":
        run_cmd(["pnpm", "run", "tauri:build"], cwd=installer_dir)
    else:
        # 不走 `pnpm run tauri -- build`：pnpm 会把 `--` 原样转发，tauri CLI(clap) 拒绝 `-- build`。
        run_cmd(["pnpm", "run", "tauri:build:nobundle"], cwd=installer_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    if target == "mac":
        final_dmg = find_installer_dmg(installer_dir, args.version)
        final_name = f"SpiritAgent-Setup-{args.version}.dmg"
        shutil.copy2(final_dmg, output_dir / final_name)
        print(f"\n==> Final installer: {output_dir / final_name}")
    else:
        target_dir = repo_root / "installer" / "src-tauri" / "target" / "release"
        exe_file = target_dir / "SpiritAgent-Setup.exe"
        if not exe_file.is_file():
            print(f"error: Tauri build did not produce {exe_file}", file=sys.stderr)
            return 1
        final_name = f"SpiritAgent-Setup-{args.version}.exe"
        shutil.copy2(exe_file, output_dir / final_name)
        print(f"\n==> Final installer: {output_dir / final_name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
