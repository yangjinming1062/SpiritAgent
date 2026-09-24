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
COPY runner/tools/ /runner/tools/

EXPOSE 10620

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10620"]
