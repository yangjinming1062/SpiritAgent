#!/usr/bin/env python3
"""scripts/build.py —— SpiritAgent 跨平台客户端构建总入口。

单一入口，端到端编排：
1. 版本同步 (client/package.json, installer/package.json, tauri.conf.json, Cargo.toml, runner/pyproject.toml)
2. 构建 runner wheel (uv sync -> pytest -> uv build --wheel)
3. 构建 desktop 产物 (pnpm test -> electron-builder)
4. 暂存 payload 到 installer/payload/
5. 签名桌面产物 (macOS codesign/notarytool, Windows signtool)
6. 临时 patch installer/src-tauri/tauri.conf.json 的 bundle.resources
7. Tauri 构建安装器 (确保 finally 还原 tauri.conf.json)
8. 拷贝最终安装器至 release/，并在 Windows 下构建自更新 zip
"""

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# 引入共享构建助手
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from build_helpers import (  # noqa: E402
    find_desktop_artifact,
    get_repo_root,
    patch_tauri_config,
    restore_tauri_config,
    set_version,
    stage_payload,
)


def run_cmd(
    cmd: list[str] | str,
    cwd: Path | None = None,
    shell: bool = False,
    env: dict[str, str] | None = None,
) -> None:
    display_cmd = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"==> [exec] {display_cmd} (in {cwd or '.'})")
    ret = subprocess.run(cmd, cwd=cwd, shell=shell, env=env, check=False)
    if ret.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {ret.returncode}: {display_cmd}")


def check_required_tools(tools: list[str]) -> None:
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        raise RuntimeError(f"Required build dependencies not found in PATH: {', '.join(missing)}")


def build_runner(repo_root: Path) -> None:
    print("==> Building runner (uv build wheel → dist/spiritagent-agent-*.whl)")
    runner_dir = repo_root / "runner"
    run_cmd(["uv", "sync", "--frozen", "--extra", "dev"], cwd=runner_dir)
    # 项目规范不提交测试代码（RULES.md §测试）；tests/ 仅在本地临时存在时才跑。
    if (runner_dir / "tests").is_dir():
        try:
            run_cmd(["uv", "run", "--frozen", "--no-sync", "pytest", "tests/", "-q"], cwd=runner_dir)
        except RuntimeError as exc:
            raise RuntimeError(
                "runner test suite failed — see pytest output. Fix the env (try `uv cache clean` + `uv sync`) before retrying the build.",
            ) from exc
    else:
        print("==> No runner tests/ directory — skipping pytest (project ships no test code)")
    run_cmd(["uv", "build", "--wheel", "--out-dir", "dist"], cwd=runner_dir)
    # 发布质量门：wheel 必须满足 server.py 本地导入；不一致直接失败，不在安装期回滚。
    run_cmd([sys.executable, str(repo_root / "scripts" / "check_runner_facade.py")], cwd=repo_root)


def build_desktop(repo_root: Path, target: str) -> None:
    print(f"==> Building client for target: {target}")
    client_dir = repo_root / "client"
    run_cmd(["pnpm", "install", "--frozen-lockfile"], cwd=client_dir)
    run_cmd(["pnpm", "test"], cwd=client_dir)

    pnpm_target = "dist:mac:dmg" if target == "mac" else "dist:win:nsis"
    run_cmd(["pnpm", "run", pnpm_target], cwd=client_dir)


def main() -> int:
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

    # 推断与校验 target
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

    # 依赖检查
    check_required_tools(["uv", "pnpm", "node"])
    if target == "mac":
        check_required_tools(["hdiutil", "codesign"])

    # 1. 统一写入版本
    set_version(args.version, repo_root)

    # 2. 构建 runner
    if not args.skip_runner:
        build_runner(repo_root)
    else:
        print("==> Skipping runner build (--skip-runner)")

    # 3. 构建 desktop
    if not args.skip_desktop:
        build_desktop(repo_root, target)
    else:
        print("==> Skipping desktop build (--skip-desktop)")

    # 4. 定位桌面端产物
    desktop_artifact = find_desktop_artifact(target, args.version, repo_root)
    if not desktop_artifact or not desktop_artifact.is_file():
        print(
            f"error: desktop artifact for target={target} version={args.version} not found in client/release/",
            file=sys.stderr,
        )
        return 1
    print(f"==> Desktop artifact: {desktop_artifact}")

    # 5. 暂存 payload
    stage_payload(repo_root, target=target)
    dest_desktop_path = repo_root / "installer" / "payload" / "client" / desktop_artifact.name
    shutil.copy2(desktop_artifact, dest_desktop_path)

    # 6. 桌面产物代码签名
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
        shutil.copy2(desktop_artifact, dest_desktop_path)
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

    # 7. Tauri 构建安装器 (确保异常或退出时还原配置)
    installer_dir = repo_root / "installer"
    patch_tauri_config(repo_root)
    try:
        print("==> Tauri build")
        run_cmd(["pnpm", "install", "--frozen-lockfile"], cwd=installer_dir)
        if target == "mac":
            run_cmd(["pnpm", "run", "tauri:build"], cwd=installer_dir)
        else:
            run_cmd(["pnpm", "run", "tauri", "--", "build", "--no-bundle"], cwd=installer_dir)
    finally:
        restore_tauri_config(repo_root)

    # 8. 收集最终安装器
    output_dir.mkdir(parents=True, exist_ok=True)
    if target == "mac":
        bundle_dir = repo_root / "installer" / "src-tauri" / "target" / "release" / "bundle" / "dmg"
        dmg_files = sorted(bundle_dir.glob(f"SpiritAgent-Setup_{args.version}_*.dmg"))
        if not dmg_files:
            dmg_files = sorted(bundle_dir.glob(f"SpiritAgent_{args.version}_*.dmg"))
        if not dmg_files:
            dmg_files = sorted(bundle_dir.glob("*.dmg"))
        if not dmg_files:
            print(f"error: Tauri build did not produce dmg in {bundle_dir}", file=sys.stderr)
            return 1
        final_dmg = dmg_files[0]
        final_name = f"SpiritAgent-Setup-{args.version}.dmg"
        shutil.copy2(final_dmg, output_dir / final_dmg.name)
        if final_dmg.name != final_name:
            shutil.copy2(final_dmg, output_dir / final_name)
        print(f"\n==> Final installer: {output_dir / final_name} (also {output_dir / final_dmg.name})")
    else:
        target_dir = repo_root / "installer" / "src-tauri" / "target" / "release"
        exe_file = target_dir / "SpiritAgent-Setup.exe"
        if not exe_file.is_file():
            print(f"error: Tauri build did not produce {exe_file}", file=sys.stderr)
            return 1
        final_name = f"SpiritAgent-Setup-{args.version}.exe"
        shutil.copy2(exe_file, output_dir / final_name)
        print(f"\n==> Final installer: {output_dir / final_name}")

        # Windows 自更新 zip 制作
        update_manifest_script = SCRIPT_DIR / "lib" / "UpdateManifest.ps1"
        runner_payload = repo_root / "installer" / "payload" / "runner"
        wheels = list(runner_payload.glob("*.whl"))
        server_py = runner_payload / "server.py"
        ps_bin = shutil.which("pwsh") or shutil.which("powershell")

        if update_manifest_script.is_file() and ps_bin and wheels and server_py.is_file():
            build_update_ps = SCRIPT_DIR / "build_client.ps1"
            if build_update_ps.is_file():
                print("==> Building update zip via PowerShell...")
                client_release = repo_root / "client" / "release"
                pw_cmd = [
                    ps_bin,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    f"& {{ . '{build_update_ps}'; Build-UpdateZip -Version '{args.version}' -DesktopReleaseDir '{client_release}' -RunnerWheelPath '{wheels[0]}' -ServerPyPath '{server_py}' -OutputDir '{output_dir}' }}",
                ]
                subprocess.run(pw_cmd, cwd=repo_root, check=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
