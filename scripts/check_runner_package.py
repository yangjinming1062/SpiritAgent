#!/usr/bin/env python3
"""构建期门禁：在仓库外隔离安装 Runner wheel，验证模块入口、版本、依赖与静态工具定义。"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

PACKAGE = "spirit_agent_runner"
PACKAGE_DIR = Path(__file__).resolve().parent.parent / "runner" / PACKAGE


def find_latest_wheel(runner_dir: Path) -> Path:
    dist = runner_dir / "dist"
    wheels = sorted(dist.glob("spirit_agent-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise SystemExit(f"no spirit_agent wheel found in {dist} (run uv build first)")
    return wheels[-1]


def wheel_version(wheel: Path, package_dir: Path = PACKAGE_DIR) -> str:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        required = {
            "spirit_agent_runner/__init__.py",
            "spirit_agent_runner/__main__.py",
            "spirit_agent_runner/bootstrap.py",
        }
        missing = required.difference(names)
        if missing:
            raise SystemExit(f"wheel missing package entry files: {sorted(missing)}")
        unexpected = [
            name
            for name in names
            if name.lower().endswith(".py")
            and name.split("/")[0] != PACKAGE
            and not name.split("/")[0].endswith(".dist-info")
        ]
        if unexpected:
            raise SystemExit(f"wheel contains Python files outside the Runner package: {unexpected}")
        source_files = {
            f"{PACKAGE}/{path.relative_to(package_dir).as_posix()}": path
            for path in package_dir.rglob("*.py")
            if "__pycache__" not in path.parts
        }
        if not source_files:
            raise SystemExit(f"Runner package source not found: {package_dir}")
        packaged_files = {name for name in names if name.startswith(f"{PACKAGE}/") and name.lower().endswith(".py")}
        missing_source = source_files.keys() - packaged_files
        extra_source = packaged_files - source_files.keys()
        if missing_source or extra_source:
            raise SystemExit(
                f"wheel/source file list mismatch: missing={sorted(missing_source)}, extra={sorted(extra_source)}",
            )
        stale_source = [name for name, path in source_files.items() if archive.read(name) != path.read_bytes()]
        if stale_source:
            raise SystemExit(f"wheel contains stale package source: {stale_source}")
        metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise SystemExit("wheel must contain exactly one distribution metadata file")
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
        if metadata.get("Name", "").lower().replace("_", "-") != "spirit-agent":
            raise SystemExit("wheel distribution must be spirit-agent")
        version = metadata.get("Version")
        if not version or not wheel.name.startswith(f"spirit_agent-{version}-"):
            raise SystemExit("wheel filename and distribution version disagree")
        return version


def check(wheel: Path) -> None:
    if not wheel.is_file():
        raise SystemExit(f"wheel not found: {wheel}")
    expected_version = wheel_version(wheel)
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv not found in PATH")

    with tempfile.TemporaryDirectory(prefix="spiritagent-package-") as tmp:
        tmp_path = Path(tmp)
        venv_dir = tmp_path / "venv"
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH"}}
        env["SPIRITAGENT_HOME"] = str(tmp_path / "home")
        print(f"==> check_runner_package: {wheel.name}", flush=True)
        subprocess.run([uv, "venv", "--python", "3.13", str(venv_dir)], cwd=tmp_path, env=env, check=True)
        python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([uv, "pip", "install", "--python", str(python), str(wheel)], cwd=tmp_path, env=env, check=True)
        command = [str(python), "-I", "-m", "spirit_agent_runner"]
        version = subprocess.run(
            [*command, "--version"],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if version != expected_version:
            raise SystemExit(f"package entry version {version!r} does not match wheel {expected_version}")
        result = subprocess.run(
            [*command, "--check"],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        report = json.loads(result.stdout)
        if report.get("version") != expected_version or report.get("tool_count", 0) <= 0:
            raise SystemExit(f"invalid package health report: {report}")
        if (tmp_path / "home").exists():
            raise SystemExit("package health checks must not create runtime data")
        print(f"runner package ok: version={version}, tools={report['tool_count']}")


def main() -> int:
    runner_dir = Path(__file__).resolve().parent.parent / "runner"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="path to built wheel (default: newest in runner/dist)")
    args = parser.parse_args()
    wheel = args.wheel.resolve() if args.wheel else find_latest_wheel(runner_dir)
    check(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
