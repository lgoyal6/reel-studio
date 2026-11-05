# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 libgomp1 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin reel

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REEL_DATA_DIR=/data/reel-studio \
    REEL_STUDIO_AUDIOSEP_HOME=/data/models/audiosep \
    REEL_STUDIO_DEEPFILTER_HOME=/data/models/deepfilter \
    UV_CACHE_DIR=/data/cache/uv \
    TORCH_HOME=/data/cache/torch \
    PORT=7860

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.9.6 /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock ./
COPY backend/ ./backend/
RUN uv sync --frozen --no-dev --no-install-project \
    && mkdir -p /data/reel-studio /data/models/audiosep /data/models/deepfilter /data/cache/uv /data/cache/torch \
    && chown -R reel:reel /data
COPY --from=frontend /build/frontend/dist ./frontend/dist
COPY scripts/container_start.py ./scripts/container_start.py

USER reel
EXPOSE 7860
CMD ["uv", "run", "--frozen", "--no-sync", "python", "scripts/container_start.py"]
