#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["modal>=1.5.2,<2"]
# ///

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import modal


ROOT = Path(__file__).resolve().parent
PORT = 8080
PUBLIC_ENV = {
    "PORT": str(PORT),
    "PDF_COMPRESSOR_PUBLIC_MODE": "1",
    "PDF_COMPRESSOR_MAX_UPLOAD_MB": "30",
    "PDF_COMPRESSOR_MAX_PAGES": "100",
    "PDF_COMPRESSOR_UPLOAD_TIMEOUT_SECONDS": "120",
    "PDF_COMPRESSOR_PROCESSING_TIMEOUT_SECONDS": "300",
    "PDF_COMPRESSOR_DOWNLOAD_TIMEOUT_SECONDS": "120",
    "PDF_COMPRESSOR_RATE_LIMIT_PER_MINUTE": "12",
    "PDF_COMPRESSOR_CONCURRENCY": "1",
    "PYTHONPATH": "/app/src",
}

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_sync(
        uv_project_dir=ROOT,
        frozen=True,
        uv_version="0.9.28",
    )
    .add_local_dir(
        ROOT / "src",
        remote_path="/app/src",
        copy=True,
    )
)
app = modal.App("papersqueeze")


@app.function(
    image=image,
    env=PUBLIC_ENV,
    cpu=2.0,
    memory=2048,
    min_containers=0,
    max_containers=1,
    scaledown_window=60,
    timeout=600,
)
@modal.concurrent(max_inputs=8)
@modal.web_server(PORT, startup_timeout=120)
def serve() -> None:
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "file_compressor.cli",
            "web",
            "--host",
            "0.0.0.0",
            "--port",
            str(PORT),
        ]
    )
