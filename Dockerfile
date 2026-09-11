# syntax=docker/dockerfile:1

# Multi-stage: the build stage carries compilers and the pip cache; the runtime
# stage gets only the installed virtualenv, which keeps the image small and
# leaves no build toolchain in the shipped container.

FROM python:3.11-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /build

# Dependencies are installed from the pinned lockfile before the source is
# copied, so editing code does not invalidate the dependency layer.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-deps .


# Development image (docker compose --profile dev up). Same dependency layer as
# runtime, but the package is installed *editable*: pip writes a path hook into
# /opt/venv that resolves `llmwiki` to /app/src at import time. Compose then
# bind-mounts the working tree over /app, so the code the container imports is
# the code on the host - edit a file, uvicorn --reload restarts it, no rebuild.
# A rebuild is only needed when the dependency set changes (requirements.txt).
FROM python:3.11-slim AS dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    LOCAL_STORAGE_PATH=/data

COPY --from=build /opt/venv /opt/venv

# No fixed user here, unlike runtime: compose runs this image as the host's own
# uid/gid (DEV_UID/DEV_GID) so files written into the bind-mounted tree and into
# ./.data belong to the developer rather than to root. /data is created world-
# writable for the case where that mount is absent.
RUN mkdir -p /data && chmod 0777 /data

WORKDIR /app

# Only the metadata the editable install needs. src/ is copied so the install
# has something to point at during the build; at run time the bind mount
# replaces it wholesale.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-deps -e . && chmod -R a+rX /opt/venv

EXPOSE 8000

# --reload-dir keeps the watcher off .git/, .data/ and .venv/, which the
# whole-tree bind mount also exposes. watchfiles is already in requirements.txt,
# so this is a real inotify watcher, not uvicorn's stat-polling fallback.
CMD ["uvicorn", "llmwiki.api.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--reload", "--reload-dir", "/app/src"]


FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    LOCAL_STORAGE_PATH=/data

# Runs as a non-root user: the service accepts uploads from the network, and a
# container that writes as root is a larger blast radius than it needs to be.
RUN useradd --create-home --uid 10001 llmwiki \
    && mkdir -p /data \
    && chown llmwiki:llmwiki /data

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY --chown=llmwiki:llmwiki scripts/ ./scripts/
COPY --chown=llmwiki:llmwiki tests/fixtures/ ./tests/fixtures/

# skills/ lives outside the package by design (v1.4 4.8.2: an operator-editable
# discovery directory), so unlike chains/prompts/*.md it is NOT package data and
# a pip install does not carry it into /opt/venv. Without this COPY the default
# AGENT_SKILLS_DIR=./skills resolves to a /app/skills that does not exist, and
# the query agent silently falls back to the fixed answer_query prompt - the
# 2026-09-10 Hostinger symptom.
#
# config/ ships the same way: providers.py and ops.py name only *which env var*
# carries each key (the values stay in .env), so they are tracked in git and
# baked in rather than copied onto every box by hand. Consequence to know: the
# routing table is then part of the image, so changing a model is a rebuild and
# push, not an edit on the VPS. docker-compose.yml has a commented-out
# ./config:/app/config:ro override for when that trade is wrong.
COPY --chown=llmwiki:llmwiki skills/ ./skills/
COPY --chown=llmwiki:llmwiki config/ ./config/

USER llmwiki
EXPOSE 8000
VOLUME ["/data"]

# Hits the app's own health endpoint. /healthz makes no network calls, so an
# unhealthy result means the process is genuinely wedged, not that Cloudflare is.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "llmwiki.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
