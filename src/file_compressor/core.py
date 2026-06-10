from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Union

from .archive import create_zip
from .images import compress_image, output_suffix_for_image
from .models import CompressionConfig, CompressionResult, CompressionSummary
from .pdfs import compress_pdf
from .utils import clamp_quality, is_image, is_pdf, iter_supported_files, relative_output_path, unique_path


def compress_path(source: Union[Path, str], config: CompressionConfig, output: Optional[Union[Path, str]] = None) -> CompressionSummary:
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    output_path = Path(output).expanduser().resolve() if output is not None else None
    if config.archive == "zip":
        return _compress_to_zip(source_path, config, output_path)
    if config.archive not in {None, ""}:
        raise ValueError("archive must be zip or omitted")
    return _compress_files(source_path, config, output_path)


def _compress_files(source: Path, config: CompressionConfig, output: Optional[Path]) -> CompressionSummary:
    root = source if source.is_dir() else source.parent
    output_dir = output if output is not None and source.is_dir() else config.output_dir
    if output is not None and source.is_file() and output.suffix == "":
        output_dir = output
        output = None
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()
    results: list[CompressionResult] = []

    files = list(iter_supported_files(source))
    if source.is_file() and output is not None:
        files = [source]

    for file_path in files:
        try:
            original_size = file_path.stat().st_size
            explicit_output = output if source.is_file() else None
            target = _output_for_file(file_path, root, output_dir, explicit_output, config)
            written = _compress_one(file_path, target, config)
            results.append(
                CompressionResult(
                    source=file_path,
                    output=written,
                    original_size=original_size,
                    compressed_size=written.stat().st_size,
                    status="ok",
                )
            )
        except Exception as exc:
            try:
                orig_size = file_path.stat().st_size
            except OSError:
                orig_size = 0
            results.append(
                CompressionResult(
                    source=file_path,
                    output=None,
                    original_size=orig_size,
                    compressed_size=None,
                    status="failed",
                    error=f"Compression failed: {exc}",
                )
            )
    return CompressionSummary(results=results)


def _compress_to_zip(source: Path, config: CompressionConfig, output: Optional[Path]) -> CompressionSummary:
    archive_output = output or config.output_dir / f"{source.stem if source.is_file() else source.name}.zip"
    if not archive_output.is_absolute():
        archive_output = (Path.cwd() / archive_output).resolve()
    archive_output = unique_path(archive_output, config.overwrite)
    if config.target_bytes is None:
        qualities = [config.quality]
    else:
        qualities = _archive_quality_candidates(config.quality, config.target_bytes)
    best_summary: Optional[CompressionSummary] = None
    best_archive: Optional[Path] = None
    best_size: Optional[int] = None

    with TemporaryDirectory(prefix="file_compressor_zip_") as temp_dir:
        temp_root = Path(temp_dir)
        for attempt, quality in enumerate(qualities):
            staging = temp_root / f"attempt_{attempt}"
            staging.mkdir(parents=True, exist_ok=True)
            attempt_config = _archive_attempt_config(config, quality, attempt)
            summary = _compress_files(source, attempt_config, staging)
            candidate = temp_root / f"candidate_{attempt}.zip"
            create_zip(staging, candidate)
            size = candidate.stat().st_size
            archive_result = CompressionResult(
                source=source,
                output=archive_output,
                original_size=sum(result.original_size for result in summary.results),
                compressed_size=size,
                status="ok" if all(result.status == "ok" for result in summary.results) else "partial",
            )
            summary.archive = archive_result
            if best_size is None or size < best_size:
                best_summary = summary
                best_archive = candidate
                best_size = size
            if config.target_bytes is not None and size <= config.target_bytes:
                break
        if best_summary is None or best_archive is None:
            raise RuntimeError("Archive compression produced no output")
        archive_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best_archive, archive_output)
        partial = not all(r.status == "ok" for r in best_summary.results)
        if config.target_bytes is not None:
            status = "best_over_target_partial" if partial else "best_over_target"
        else:
            status = "partial" if partial else "ok"
        best_summary.archive = CompressionResult(
            source=source,
            output=archive_output,
            original_size=sum(result.original_size for result in best_summary.results),
            compressed_size=archive_output.stat().st_size,
            status=status,
        )
        return best_summary


def _compress_one(source: Path, output: Path, config: CompressionConfig) -> Path:
    if is_image(source):
        return compress_image(source, output, config)
    if is_pdf(source):
        return compress_pdf(source, output, config)
    raise ValueError(f"Unsupported file type: {source}")


def _output_for_file(source: Path, root: Path, output_dir: Path, explicit_output: Optional[Path], config: CompressionConfig) -> Path:
    if explicit_output is not None and source.is_file():
        explicit_output.parent.mkdir(parents=True, exist_ok=True)
        return unique_path(explicit_output, config.overwrite)
    suffix = output_suffix_for_image(source, config) if is_image(source) else source.suffix.lower()
    return relative_output_path(source, root, output_dir, suffix, config.overwrite)


def _archive_quality_candidates(start: int, target_bytes: Optional[int]) -> list[int]:
    start = clamp_quality(start)
    if target_bytes is None:
        return [start]
    if start <= 1:
        return [start]
    base = [v for v in [20, 18, 16] if v <= start]
    values = sorted(set(list(range(start, 15, -4)) + [start] + base), reverse=True)
    return values


_ARCHIVE_DPI_VALUES: list[int] = [140, 120, 110, 100, 90, 80, 72, 65, 60, 55, 50, 45, 40, 36]
_ARCHIVE_FALLBACK_EDGES: list[Optional[int]] = [None, 1800, 1600, 1400, 1200, 1000, 800, 640]


def _archive_attempt_config(config: CompressionConfig, quality: int, attempt: int) -> CompressionConfig:
    lower = [d for d in _ARCHIVE_DPI_VALUES if d < config.pdf_dpi]
    dpi_values = [config.pdf_dpi] + lower
    if config.max_edge is not None:
        fallback_edges: list[Optional[int]] = [None] + [max(64, int(config.max_edge * m)) for m in (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3)]
    else:
        fallback_edges = _ARCHIVE_FALLBACK_EDGES
    dpi = dpi_values[min(attempt, len(dpi_values) - 1)]
    fallback_edge = fallback_edges[min(attempt, len(fallback_edges) - 1)]
    if config.max_edge is not None:
        edge = min(config.max_edge, fallback_edge) if fallback_edge is not None else config.max_edge
    else:
        edge = fallback_edge
    pdf_mode = config.pdf_mode
    if config.target_bytes is not None and attempt > 0 and pdf_mode == "auto":
        pdf_mode = "raster"
    return replace(
        config,
        quality=quality,
        target_bytes=None,
        output_dir=Path("."),
        pdf_dpi=max(36, int(dpi)),
        max_edge=edge,
        pdf_mode=pdf_mode,
        archive=None,
    )
