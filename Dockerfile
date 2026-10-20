# syntax=docker/dockerfile:1.7

FROM node:22.20-bookworm-slim AS frontend-builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY frontend ./frontend
COPY tsconfig.json eslint.config.mjs ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.17 AS uv

FROM python:3.14-slim-bookworm AS runtime
ARG GEOPLAN_BUILD_REVISION=unknown
ENV GEOPLAN_BUILD_REVISION=$GEOPLAN_BUILD_REVISION
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/backend/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./backend/
RUN uv sync --project backend --locked --extra optimization --no-dev --no-install-project

COPY backend/src ./backend/src
RUN uv sync --project backend --locked --extra optimization --no-dev
COPY --from=frontend-builder /app/dist ./dist

RUN useradd --create-home --uid 10001 geoplan \
    && mkdir -p /app/work/server-data \
    && chown -R geoplan:geoplan /app
USER geoplan

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "geoplan.bootstrap:app", "--host", "0.0.0.0", "--port", "8000"]
