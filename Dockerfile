# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.9.28 AS uv

FROM python:3.12.12-slim-bookworm AS builder
COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.12.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.source="https://github.com/asimfish/pdf-image-compressor" \
      org.opencontainers.image.description="Target-aware PDF compression with searchable text preservation" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    PDF_COMPRESSOR_PUBLIC_MODE=1 \
    PDF_COMPRESSOR_MAX_UPLOAD_MB=30 \
    PDF_COMPRESSOR_MAX_PAGES=100 \
    PDF_COMPRESSOR_UPLOAD_TIMEOUT_SECONDS=120 \
    PDF_COMPRESSOR_PROCESSING_TIMEOUT_SECONDS=300 \
    PDF_COMPRESSOR_DOWNLOAD_TIMEOUT_SECONDS=120 \
    PDF_COMPRESSOR_RATE_LIMIT_PER_MINUTE=12 \
    PDF_COMPRESSOR_CONCURRENCY=1

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ['PORT'] + '/api/health', timeout=3)"

CMD ["/bin/sh", "-c", "exec file-compressor web --host 0.0.0.0 --port \"$PORT\""]
