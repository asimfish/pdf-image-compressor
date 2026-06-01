from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from file_compressor.cli import build_parser, print_summary, _format_result, _summary_to_json
from file_compressor.models import CompressionResult, CompressionSummary


# ── build_parser ──

def test_parser_compress_defaults():
    parser = build_parser()
    args = parser.parse_args(["compress", "input.pdf"])
    assert args.command == "compress"
    assert args.input == Path("input.pdf")
    assert args.output is None
    assert args.output_dir == Path("compressed")
    assert args.quality == 82
    assert args.max_edge is None
    assert args.to_webp is False
    assert args.target_size is None
    assert args.overwrite is False
    assert args.archive is None
    assert args.pdf_mode == "auto"
    assert args.pdf_dpi == 120
    assert args.pdf_grayscale is False
    assert args.keep_metadata is False
    assert args.json_report is False


def test_parser_compress_all_options():
    parser = build_parser()
    args = parser.parse_args([
        "compress", "input.pdf",
        "--output", "out.pdf",
        "--output-dir", "mydir",
        "--quality", "50",
        "--max-edge", "1024",
        "--to-webp",
        "--target-size", "500KB",
        "--overwrite",
        "--archive", "zip",
        "--pdf-mode", "raster",
        "--pdf-dpi", "150",
        "--pdf-grayscale",
        "--keep-metadata",
        "--json-report",
    ])
    assert args.output == Path("out.pdf")
    assert args.output_dir == Path("mydir")
    assert args.quality == 50
    assert args.max_edge == 1024
    assert args.to_webp is True
    assert args.target_size == "500KB"
    assert args.overwrite is True
    assert args.archive == "zip"
    assert args.pdf_mode == "raster"
    assert args.pdf_dpi == 150
    assert args.pdf_grayscale is True
    assert args.keep_metadata is True
    assert args.json_report is True


def test_parser_web_defaults():
    parser = build_parser()
    args = parser.parse_args(["web"])
    assert args.command == "web"
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.data_dir == Path.home() / ".pdf-manager"


def test_parser_web_custom():
    parser = build_parser()
    args = parser.parse_args(["web", "--host", "0.0.0.0", "--port", "9000", "--data-dir", "/tmp/lib"])
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.data_dir == Path("/tmp/lib")


def test_parser_no_command():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.command is None


# ── _format_result ──

def test_format_result_ok():
    result = CompressionResult(
        source=Path("input.pdf"),
        output=Path("output.pdf"),
        original_size=100_000,
        compressed_size=50_000,
        status="ok",
    )
    text = _format_result(result)
    assert "OK" in text
    assert "50.0%" in text
    assert "100.0 KB" in text
    assert "50.0 KB" in text


def test_format_result_failed():
    result = CompressionResult(
        source=Path("bad.pdf"),
        output=None,
        original_size=100,
        compressed_size=None,
        status="failed",
        error="unsupported format",
    )
    text = _format_result(result)
    assert "FAILED" in text
    assert "unsupported format" in text


def test_format_result_no_ratio():
    result = CompressionResult(
        source=Path("x.pdf"),
        output=Path("x_out.pdf"),
        original_size=100,
        compressed_size=100,
        status="ok",
    )
    text = _format_result(result)
    assert "0.0%" in text


def test_format_result_best_over_target():
    result = CompressionResult(
        source=Path("big.pdf"),
        output=Path("big_out.pdf"),
        original_size=200_000,
        compressed_size=180_000,
        status="best_over_target",
    )
    text = _format_result(result, label="archive")
    assert "OK archive" in text


# ── print_summary ──

def test_print_summary(capsys):
    summary = CompressionSummary(results=[
        CompressionResult(
            source=Path("a.pdf"), output=Path("a_out.pdf"),
            original_size=1000, compressed_size=500, status="ok",
        ),
    ])
    print_summary(summary)
    captured = capsys.readouterr()
    assert "OK file" in captured.out
    assert "50.0%" in captured.out


def test_print_summary_with_archive(capsys):
    summary = CompressionSummary(
        results=[
            CompressionResult(
                source=Path("a.pdf"), output=Path("a_out.pdf"),
                original_size=1000, compressed_size=500, status="ok",
            ),
        ],
        archive=CompressionResult(
            source=Path("dir"), output=Path("dir.zip"),
            original_size=1000, compressed_size=400, status="ok",
        ),
    )
    print_summary(summary)
    captured = capsys.readouterr()
    assert "OK archive" in captured.out


# ── _summary_to_json ──

def test_summary_to_json():
    summary = CompressionSummary(results=[
        CompressionResult(
            source=Path("a.pdf"), output=Path("a_out.pdf"),
            original_size=1000, compressed_size=500, status="ok",
        ),
    ])
    data = _summary_to_json(summary)
    assert "results" in data
    assert len(data["results"]) == 1
    assert data["results"][0]["saved_bytes"] == 500
    assert data["archive"] is None


def test_summary_to_json_with_archive():
    summary = CompressionSummary(
        results=[],
        archive=CompressionResult(
            source=Path("dir"), output=Path("dir.zip"),
            original_size=2000, compressed_size=800, status="ok",
        ),
    )
    data = _summary_to_json(summary)
    assert data["archive"] is not None
    assert data["archive"]["compression_ratio"] == pytest.approx(0.6)


def test_summary_to_json_roundtrip():
    summary = CompressionSummary(
        results=[
            CompressionResult(
                source=Path("x.pdf"), output=Path("x_out.pdf"),
                original_size=5000, compressed_size=2500, status="ok",
            ),
        ],
        archive=CompressionResult(
            source=Path("dir"), output=Path("dir.zip"),
            original_size=5000, compressed_size=2000, status="ok",
        ),
    )
    data = _summary_to_json(summary)
    serialized = json.dumps(data)
    parsed = json.loads(serialized)
    assert parsed["results"][0]["compression_ratio"] == 0.5
    assert parsed["archive"]["saved_bytes"] == 3000
