FROM scratch AS review-models
ADD --chmod=0444 --checksum=sha256:8f4c659275977e7a3bfbfa339a9c769ad793df50f9c0baa8c14b11baa1646430 https://raw.githubusercontent.com/Linzaer/Ultra-Light-Fast-Generic-Face-Detector-1MB/cc497be475371d891d5795e46fc80ebaddf683c5/models/onnx/version-RFB-640.onnx /face-detector-rfb-640.onnx
ADD --chmod=0444 --checksum=sha256:21f0df2b54c9fef1b4f4061dd0b153bc72f071b5ce9fc04234f13ebfd093b44c https://raw.githubusercontent.com/Linzaer/Ultra-Light-Fast-Generic-Face-Detector-1MB/cc497be475371d891d5795e46fc80ebaddf683c5/LICENSE /UltraFace-LICENSE
ADD --chmod=0444 --checksum=sha256:1ad4f8d6c2f776a9967db3993c9ca740bc350104f9d37c151dc183fc29a464ad https://huggingface.co/Xenova/movenet-singlepose-lightning/resolve/ed0f314bb7356fd1dbf1e4f52c2d40791bf6534f/onnx/model.onnx /movenet-singlepose-lightning.onnx

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
COPY --from=review-models / /opt/spiritagent/models/
RUN cp /usr/share/common-licenses/Apache-2.0 /opt/spiritagent/models/MoveNet-LICENSE

# 版本参数只在构建末尾声明，避免提交号变化使系统和 Python 依赖层缓存失效。
ARG GIT_COMMIT=unknown
LABEL org.opencontainers.image.revision=${GIT_COMMIT}

EXPOSE 10620

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10620"]
