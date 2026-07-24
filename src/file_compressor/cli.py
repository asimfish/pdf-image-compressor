from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .core import compress_path
from .models import CompressionConfig, CompressionResult, CompressionSummary
from .utils import format_size, parse_size


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "compress":
        run_compress(args)
    elif args.command == "web":
        run_web(args)
    else:
        parser.print_help()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="file-compressor")
    subparsers = parser.add_subparsers(dest="command")

    compress = subparsers.add_parser("compress", help="Compress PDFs and images")
    compress.add_argument("input", type=Path)
    compress.add_argument("--output", "-o", type=Path, default=None)
    compress.add_argument("--output-dir", type=Path, default=Path("compressed"))
    compress.add_argument("--quality", type=int, default=82)
    compress.add_argument("--max-edge", type=int, default=None)
    compress.add_argument("--to-webp", action="store_true")
    compress.add_argument("--target-size", type=str, default=None)
    compress.add_argument("--overwrite", action="store_true")
    compress.add_argument("--archive", choices=["zip"], default=None)
    compress.add_argument("--pdf-mode", choices=["fidelity", "auto", "optimize", "raster", "text"], default="fidelity")
    compress.add_argument("--pdf-dpi", type=int, default=120)
    compress.add_argument("--pdf-grayscale", action="store_true")
    compress.add_argument("--compression-level", type=int, choices=range(1, 5), default=2)
    compress.add_argument("--keep-metadata", action="store_true")
    compress.add_argument("--json-report", action="store_true")

    web = subparsers.add_parser("web", help="Run local web UI")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--data-dir", type=Path, default=Path.home() / ".pdf-manager", help="Directory for PDF library storage")
    return parser


def run_compress(args: argparse.Namespace) -> None:
    if not 1 <= args.quality <= 95:
        raise SystemExit("quality must be between 1 and 95")
    if not 36 <= args.pdf_dpi <= 300:
        raise SystemExit("pdf_dpi must be between 36 and 300")
    if args.max_edge is not None and not (100 <= args.max_edge <= 10000):
        raise SystemExit("max_edge must be between 100 and 10000")
    try:
        target_bytes = parse_size(args.target_size)
    except ValueError as exc:
        raise SystemExit(str(exc))
    config = CompressionConfig(
        quality=args.quality,
        max_edge=args.max_edge,
        to_webp=args.to_webp,
        target_bytes=target_bytes,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
        archive=args.archive,
        pdf_mode=args.pdf_mode,
        pdf_dpi=args.pdf_dpi,
        pdf_grayscale=args.pdf_grayscale,
        strip_metadata=not args.keep_metadata,
        compression_level=args.compression_level,
    )
    summary = compress_path(args.input, config, args.output)
    if args.json_report:
        print(json.dumps(_summary_to_json(summary), ensure_ascii=False, indent=2))
    else:
        print_summary(summary)


def run_web(args: argparse.Namespace) -> None:
    import uvicorn

    from .web import init_storage
    init_storage(args.data_dir)
    print(f"PDF library storage: {args.data_dir}")
    uvicorn.run("file_compressor.web:app", host=args.host, port=args.port, reload=False)


def print_summary(summary: CompressionSummary) -> None:
    for result in summary.results:
        print(_format_result(result))
    if summary.archive is not None:
        print(_format_result(summary.archive, label="archive"))


def _format_result(result: CompressionResult, label: str = "file") -> str:
    if result.status not in {"ok", "partial", "best_over_target", "best_over_target_partial"}:
        return f"FAILED {label}: {result.source} -> {result.error}"
    ratio = result.compression_ratio
    ratio_text = "-" if ratio is None else f"{ratio * 100:.1f}%"
    prefix = "OK (over target)" if result.status in {"best_over_target", "best_over_target_partial"} else "OK"
    return f"{prefix} {label}: {result.source} -> {result.output} | {format_size(result.original_size)} -> {format_size(result.compressed_size)} | saved {ratio_text}"


def _summary_to_json(summary: CompressionSummary) -> dict:
    return {
        "results": [_result_to_json(result) for result in summary.results],
        "archive": _result_to_json(summary.archive) if summary.archive else None,
    }


def _result_to_json(result: Optional[CompressionResult]) -> Optional[dict]:
    if result is None:
        return None
    data = asdict(result)
    data["source"] = str(result.source)
    data["output"] = str(result.output) if result.output else None
    data["saved_bytes"] = result.saved_bytes
    data["compression_ratio"] = result.compression_ratio
    return data


if __name__ == "__main__":
    main()
