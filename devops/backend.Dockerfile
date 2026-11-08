FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg build-essential libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

RUN apt-get update \
    && apt-get install -y --no-install-recommends libegl1 libgles2 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ .
COPY backend/config.docker.yaml config.yaml
COPY TalkingHead/animation_catalog.json /app/TalkingHead/animation_catalog.json

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONPATH="/app:/app/backend"

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "main.py"]
