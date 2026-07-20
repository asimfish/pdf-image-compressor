from __future__ import annotations

from pathlib import Path

from .core import compress_path
from .models import CompressionConfig


def compress_pdf_to_output(
    source: str,
    config: CompressionConfig,
    output: str,
) -> None:
    summary = compress_path(Path(source), config, Path(output))
    result = next((item for item in summary.results if item.output is not None), None)
    if result is None or result.output is None or not result.output.is_file():
        raise RuntimeError("Compression worker produced no output")
