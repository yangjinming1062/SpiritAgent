"""Docker / Compose 探测与后端运行状态读取；compose 在仓库 backend/ 目录执行，部署身份继承该目录的 .env。"""

import json
from collections.abc import Sequence
from typing import Any

from updater.config import UpdaterSettings
from updater.contracts import PreflightIssue, ServiceRuntime
from updater.runner import run_command

# 更新范围：只构建与重启后端。
SERVICE_NAMES = ("backend",)

# 构建时写入（backend/Dockerfile），用于核对运行镜像是否来自目标提交。
IMAGE_COMMIT_LABEL = "org.opencontainers.image.revision"
# 手工构建或未带标签的历史镜像按未知处理，不参与版本核对。
_UNKNOWN_LABELS = frozenset({"", "<no value>", "unknown"})


def compose_cwd(settings: UpdaterSettings) -> str:
    return str(settings.repo_dir / "backend")


async def _docker(settings: UpdaterSettings, *args: str, timeout: float = 60.0) -> str:
    output = await run_command("docker", *args, timeout=timeout, cwd=compose_cwd(settings))
    if output.code != 0:
        raise RuntimeError(f"docker 失败：{_last_line(output.stderr or output.stdout) or f'exit={output.code}'}")
    return output.stdout


async def toolchain_issues(settings: UpdaterSettings) -> list[PreflightIssue]:
    """docker 与 compose 可用性；探测器自身故障（如工作目录缺失）按该项问题呈现。"""

    checks: tuple[tuple[str, Sequence[str]], ...] = (
        ("docker", ("docker", "version", "--format", "{{.Server.Version}}")),
        ("compose", ("docker", "compose", "version", "--format", "{{.Version}}")),
    )
    issues: list[PreflightIssue] = []
    for check, command in checks:
        try:
            output = await run_command(*command, timeout=15.0, cwd=compose_cwd(settings))
            detail = "" if output.code == 0 else (_last_line(output.stderr) or f"{check} 不可用")
        except OSError as exc:
            detail = str(exc)
        if detail:
            issues.append(PreflightIssue(check=check, message=detail[:200]))
    return issues


async def read_backend(settings: UpdaterSettings) -> ServiceRuntime:
    """按部署项目身份读取后端服务；容器以 compose ps 为准，不猜测。"""

    entries = _parse_ps_json(await _docker(settings, "compose", "ps", "--all", "--format", "json"))
    service = ServiceRuntime(name="backend")
    info = next((entry for entry in entries if entry.get("Service") == "backend"), None)
    if info is None:
        return service
    service.state = str(info.get("State") or "unknown")
    service.health = str(info.get("Health") or "").lower() or ("none" if service.state == "running" else "unknown")
    container = str(info.get("ID") or info.get("Name") or "")
    if container:
        service.image_id = await _image_id(settings, container, info)
        service.running_commit = await _image_label(settings, service.image_id)
    return service


def _parse_ps_json(raw: str) -> list[dict[str, Any]]:
    # docker compose ps --format json 在 v2 输出 JSON Lines，部分版本输出数组。
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        return [item for item in json.loads(text) if isinstance(item, dict)]
    return [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]


async def _image_id(settings: UpdaterSettings, container: str, info: dict[str, Any]) -> str:
    try:
        raw = await _docker(settings, "container", "inspect", "--format", "{{.Image}}", container, timeout=30.0)
    except RuntimeError:
        return str(info.get("Image") or "")[:64]
    return raw.strip()[:64]


async def _image_label(settings: UpdaterSettings, image_id: str) -> str:
    if not image_id:
        return ""
    try:
        raw = await _docker(
            settings,
            "image",
            "inspect",
            "--format",
            f'{{{{ index .Config.Labels "{IMAGE_COMMIT_LABEL}" }}}}',
            image_id,
            timeout=30.0,
        )
    except RuntimeError:
        return ""
    label = raw.strip()
    return "" if label in _UNKNOWN_LABELS else label


def _last_line(text: str) -> str:
    detail = text.strip().splitlines()
    return detail[-1][:200] if detail else ""
