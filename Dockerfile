# syntax=docker/dockerfile:1.7

# --- build: resolve dependencies into a venv with uv --------------------------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# --- runtime: same base image, no build tools, unprivileged user --------------
FROM python:3.14-slim AS runtime

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid "${APP_GID}" bot \
 && useradd --system --uid "${APP_UID}" --gid bot --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin bot

WORKDIR /app
# Code stays root-owned and read-only for the runtime user.
COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY sql ./sql

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER bot:bot

EXPOSE 8080

CMD ["python", "-m", "src.main"]
