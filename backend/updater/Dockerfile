# syntax=docker/dockerfile:1.6
# 独立更新容器（compose profile updater）：检查更新、构建与重启 backend。
# 仓库按宿主机绝对路径挂载、Docker socket 接入，compose 身份在仓库内继承。
FROM docker:28-cli AS docker-cli

FROM python:3.13-slim

# docker CLI 与 compose 插件取自官方镜像：静态二进制，不必在容器内安装 Docker 守护进程。
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /srv/updater
# 复用 backend 锁文件：契约模型与 Backend 使用同一 pydantic 版本。
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project

COPY backend/updater ./updater

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD [".venv/bin/python", "-m", "updater"]
