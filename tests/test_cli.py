from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

from file_compressor.cli import build_parser, main, print_summary, run_compress, _format_result, _result_to_json, _summary_to_json
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


# ── _result_to_json edge cases ──

def test_result_to_json_none():
    assert _result_to_json(None) is None


def test_result_to_json_output_none():
    result = CompressionResult(
        source=Path("a.pdf"), output=None,
        original_size=100, compressed_size=None, status="failed",
    )
    data = _result_to_json(result)
    assert data["output"] is None
    assert data["saved_bytes"] is None
    assert data["compression_ratio"] is None


# ── _format_result edge cases ──

def test_format_result_partial():
    result = CompressionResult(
        source=Path("p.pdf"), output=Path("p_out.pdf"),
        original_size=1000, compressed_size=600, status="partial",
    )
    text = _format_result(result)
    assert "OK" in text
    assert "40.0%" in text


# ── run_compress ──

def _make_pdf(path: Path, pages: int = 3) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def test_run_compress_basic(tmp_path):
    src = tmp_path / "input.pdf"
    _make_pdf(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    args = Namespace(
        input=src, output=None, output_dir=out_dir, quality=70,
        max_edge=None, to_webp=False, target_size=None,
        overwrite=False, archive=None, pdf_mode="auto",
        pdf_dpi=120, pdf_grayscale=False, keep_metadata=False,
        json_report=False, compression_level=2,
    )
    run_compress(args)

    compressed = list(out_dir.glob("*.pdf"))
    assert len(compressed) == 1
    assert compressed[0].stat().st_size > 0


def test_run_compress_with_output(tmp_path):
    src = tmp_path / "input.pdf"
    _make_pdf(src)
    out = tmp_path / "result.pdf"

    args = Namespace(
        input=src, output=out, output_dir=tmp_path, quality=50,
        max_edge=None, to_webp=False, target_size=None,
        overwrite=False, archive=None, pdf_mode="raster",
        pdf_dpi=100, pdf_grayscale=True, keep_metadata=False,
        json_report=False, compression_level=2,
    )
    run_compress(args)
    assert out.exists()


def test_run_compress_json_report(tmp_path, capsys):
    src = tmp_path / "input.pdf"
    _make_pdf(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    args = Namespace(
        input=src, output=None, output_dir=out_dir, quality=82,
        max_edge=None, to_webp=False, target_size=None,
        overwrite=False, archive=None, pdf_mode="auto",
        pdf_dpi=120, pdf_grayscale=False, keep_metadata=False,
        json_report=True, compression_level=2,
    )
    run_compress(args)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert "results" in parsed
    assert len(parsed["results"]) == 1


# ── main dispatch ──

def test_main_compress(tmp_path):
    src = tmp_path / "input.pdf"
    _make_pdf(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with patch("sys.argv", ["file-compressor", "compress", str(src), "--output-dir", str(out_dir)]):
        main()

    compressed = list(out_dir.glob("*.pdf"))
    assert len(compressed) == 1


def test_main_no_command(capsys):
    with patch("sys.argv", ["file-compressor"]):
        main()
    captured = capsys.readouterr()
    assert "usage:" in captured.out.lower() or "file-compressor" in captured.out.lower()


def test_run_web(tmp_path):
    from unittest.mock import MagicMock
    from file_compressor.cli import run_web

    mock_uvicorn = MagicMock()
    args = Namespace(host="127.0.0.1", port=9999, data_dir=tmp_path / "webdata")
    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        run_web(args)
    mock_uvicorn.run.assert_called_once_with(
        "file_compressor.web:app", host="127.0.0.1", port=9999, reload=False,
    )


def test_main_web(tmp_path):
    from unittest.mock import MagicMock

    mock_uvicorn = MagicMock()
    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        with patch("sys.argv", ["file-compressor", "web", "--data-dir", str(tmp_path / "webdata"), "--port", "8888"]):
            main()
    mock_uvicorn.run.assert_called_once()


# ── CLI input validation ──

def test_run_compress_rejects_quality_below_1(tmp_path):
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    args = Namespace(
        input=src, output=None, output_dir=tmp_path, quality=0,
        max_edge=None, to_webp=False, target_size=None,
        overwrite=False, archive=None, pdf_mode="auto",
        pdf_dpi=120, pdf_grayscale=False, keep_metadata=False,
        json_report=False,
    )
    with pytest.raises(SystemExit, match="quality"):
        run_compress(args)


def test_run_compress_rejects_quality_above_95(tmp_path):
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    args = Namespace(
        input=src, output=None, output_dir=tmp_path, quality=100,
        max_edge=None, to_webp=False, target_size=None,
        overwrite=False, archive=None, pdf_mode="auto",
        pdf_dpi=120, pdf_grayscale=False, keep_metadata=False,
        json_report=False,
    )
    with pytest.raises(SystemExit, match="quality"):
        run_compress(args)


def test_run_compress_rejects_dpi_below_36(tmp_path):
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    args = Namespace(
        input=src, output=None, output_dir=tmp_path, quality=82,
        max_edge=None, to_webp=False, target_size=None,
        overwrite=False, archive=None, pdf_mode="auto",
        pdf_dpi=10, pdf_grayscale=False, keep_metadata=False,
        json_report=False,
    )
    with pytest.raises(SystemExit, match="pdf_dpi"):
        run_compress(args)


def test_run_compress_rejects_dpi_above_300(tmp_path):
    src = tmp_path / "in.pdf"
    _make_pdf(src)
    args = Namespace(
        input=src, output=None, output_dir=tmp_path, quality=82,
        max_edge=None, to_webp=False, target_size=None,
        overwrite=False, archive=None, pdf_mode="auto",
        pdf_dpi=500, pdf_grayscale=False, keep_metadata=False,
        json_report=False,
    )
    with pytest.raises(SystemExit, match="pdf_dpi"):
        run_compress(args)
