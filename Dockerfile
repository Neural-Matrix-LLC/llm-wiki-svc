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

USER llmwiki
EXPOSE 8000
VOLUME ["/data"]

# Hits the app's own health endpoint. /healthz makes no network calls, so an
# unhealthy result means the process is genuinely wedged, not that Cloudflare is.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "llmwiki.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
