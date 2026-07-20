# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89

FROM ghcr.io/astral-sh/uv:0.9.28@sha256:59240a65d6b57e6c507429b45f01b8f2c7c0bbeee0fb697c41a39c6a8e3a4cfb AS uv

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS builder
COPY --from=uv /uv /uvx /bin/

ARG SOURCE_DATE_EPOCH=315532800
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra build --no-install-project --no-editable

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra build --no-build-isolation --no-editable
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-build-isolation --no-editable

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime

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
