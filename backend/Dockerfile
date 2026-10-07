FROM node:24-slim AS remote-build

WORKDIR /build/remote
RUN npm install --global pnpm@11.5.2
COPY remote/package.json remote/pnpm-lock.yaml remote/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY remote/ ./
COPY shared/protocol.ts ../shared/protocol.ts
RUN pnpm build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && ffmpeg -version \
    && ffprobe -version \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY backend/pyproject.toml backend/uv.lock ./

RUN uv sync --frozen

COPY backend/ .
COPY --from=remote-build /build/remote/dist/ ./static/remote/

EXPOSE 10620

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10620"]
