FROM node:24-slim AS remote-build

WORKDIR /build/remote
RUN npm install --global pnpm@11.5.2
COPY remote/package.json remote/pnpm-lock.yaml remote/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY remote/ ./
COPY shared/ ../shared/
RUN pnpm build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

ARG DEBIAN_MIRROR=http://deb.debian.org
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && ffmpeg -version \
    && ffprobe -version \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY backend/pyproject.toml backend/uv.lock ./

RUN uv sync --frozen

COPY backend/ .
COPY --from=remote-build /build/remote/dist/ ./static/remote/

# 版本参数只在构建末尾声明，避免提交号变化使系统和 Python 依赖层缓存失效。
ARG GIT_COMMIT=unknown
LABEL org.opencontainers.image.revision=${GIT_COMMIT}

EXPOSE 10620

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10620"]
