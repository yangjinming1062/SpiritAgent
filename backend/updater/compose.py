"""Docker / Compose 探测与后端运行状态读取；compose 在仓库 backend/ 目录执行，部署身份继承该目录的 .env。"""

from collections.abc import Sequence

from infrastructure.updater_contracts import ServicePreflightIssue, ServiceRuntimeView
from updater.config import UpdaterSettings
from updater.runner import run_command

# 更新范围：只构建与重启后端。
SERVICE_NAMES = ("backend",)

# 构建时写入（backend/Dockerfile），用于核对运行镜像是否来自目标提交。
IMAGE_COMMIT_LABEL = "org.opencontainers.image.revision"
# 未带提交标签的镜像在状态页显示为版本未知。
_UNKNOWN_LABELS = frozenset({"", "<no value>", "unknown"})


def compose_cwd(settings: UpdaterSettings) -> str:
    return str(settings.repo_dir / "backend")


async def _docker(settings: UpdaterSettings, *args: str, timeout: float = 60.0) -> str:
    output = await run_command("docker", *args, timeout=timeout, cwd=compose_cwd(settings))
    if output.code != 0:
        raise RuntimeError(f"docker 失败：{_last_line(output.stderr or output.stdout) or f'exit={output.code}'}")
    return output.stdout


async def toolchain_issues(settings: UpdaterSettings) -> list[ServicePreflightIssue]:
    """docker 与 compose 可用性；探测器自身故障（如工作目录缺失）按该项问题呈现。"""

    checks: tuple[tuple[str, Sequence[str]], ...] = (
        ("docker", ("docker", "version", "--format", "{{.Server.Version}}")),
        ("compose", ("docker", "compose", "version", "--format", "{{.Version}}")),
    )
    issues: list[ServicePreflightIssue] = []
    for check, command in checks:
        try:
            output = await run_command(*command, timeout=15.0, cwd=compose_cwd(settings))
            detail = "" if output.code == 0 else (_last_line(output.stderr) or f"{check} 不可用")
        except TimeoutError:
            detail = f"{check} 探测超时"
        except OSError as exc:
            detail = str(exc)
        if detail:
            issues.append(ServicePreflightIssue(check=check, message=detail[:200]))
    return issues


async def read_backend(settings: UpdaterSettings) -> ServiceRuntimeView:
    """按部署项目身份读取后端服务；容器以 compose ps 为准，不猜测。"""

    template = '{{if ne (.Label "com.docker.compose.oneoff") "True"}}{{.ID}}{{end}}'
    ids = (await _docker(settings, "compose", "ps", "--all", "--no-trunc", "--format", template, "backend")).split()
    if not ids:
        return ServiceRuntimeView(name="backend")
    if len(ids) != 1:
        raise ValueError("部署项目存在多个 backend 容器，无法确认运行实例。")
    fields = "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.Image}}"
    raw = await _docker(settings, "container", "inspect", "--format", fields, ids[0], timeout=30.0)
    state, health, image_id = raw.strip().split("|")
    return ServiceRuntimeView(
        name="backend",
        state=state,
        health=health or ("none" if state == "running" else "unknown"),
        image_id=image_id,
        running_commit=await _image_label(settings, image_id),
    )


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
