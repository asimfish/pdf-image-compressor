from pathlib import Path

import fitz
import pytest

from file_compressor.models import CompressionConfig
from file_compressor.pdfs import compress_pdf, optimize_pdf, rasterize_pdf


def _make_pdf(path: Path, pages: int = 3, with_images: bool = False) -> Path:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1}: Hello World", fontsize=24)
        if with_images:
            # Draw a colored rectangle as "image" content
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(100, 100, 300, 300))
            shape.finish(color=(1, 0, 0), fill=(1, 0, 0))
            shape.commit()
    doc.save(path)
    doc.close()
    return path


def test_optimize_pdf_reduces_size(tmp_path: Path):
    source = _make_pdf(tmp_path / "input.pdf", pages=5)
    output = tmp_path / "optimized.pdf"
    original_size = source.stat().st_size

    config = CompressionConfig(output_dir=tmp_path)
    result = optimize_pdf(source, output, config)

    assert result.exists()
    assert output.stat().st_size <= original_size


def test_optimize_pdf_strips_metadata(tmp_path: Path):
    source = _make_pdf(tmp_path / "meta.pdf")
    output = tmp_path / "stripped.pdf"

    config = CompressionConfig(strip_metadata=True, output_dir=tmp_path)
    optimize_pdf(source, output, config)

    doc = fitz.open(output)
    meta = doc.metadata
    doc.close()
    assert meta.get("title", "") == "" or meta.get("title") is None


def test_rasterize_pdf_basic(tmp_path: Path):
    source = _make_pdf(tmp_path / "raster_in.pdf", pages=2)
    output = tmp_path / "raster_out.pdf"

    rasterize_pdf(source, output, dpi=100, quality=70, grayscale=False, strip_metadata=True)

    assert output.exists()
    doc = fitz.open(output)
    assert len(doc) == 2
    doc.close()


def test_rasterize_pdf_grayscale(tmp_path: Path):
    source = _make_pdf(tmp_path / "gray_in.pdf")
    output = tmp_path / "gray_out.pdf"

    rasterize_pdf(source, output, dpi=100, quality=60, grayscale=True, strip_metadata=True)

    assert output.exists()
    assert output.stat().st_size > 0


def test_rasterize_pdf_keep_metadata(tmp_path: Path):
    source = _make_pdf(tmp_path / "meta_in.pdf")
    output = tmp_path / "meta_out.pdf"

    rasterize_pdf(source, output, dpi=100, quality=70, grayscale=False, strip_metadata=False)

    assert output.exists()
    assert output.stat().st_size > 0


def test_compress_pdf_auto_mode(tmp_path: Path):
    source = _make_pdf(tmp_path / "auto.pdf", pages=3)
    output = tmp_path / "auto_out.pdf"

    config = CompressionConfig(pdf_mode="auto", output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()


def test_compress_pdf_optimize_mode(tmp_path: Path):
    source = _make_pdf(tmp_path / "opt.pdf")
    output = tmp_path / "opt_out.pdf"

    config = CompressionConfig(pdf_mode="optimize", output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()


def test_compress_pdf_raster_mode(tmp_path: Path):
    source = _make_pdf(tmp_path / "rast.pdf")
    output = tmp_path / "rast_out.pdf"

    config = CompressionConfig(pdf_mode="raster", pdf_dpi=100, quality=60, output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()


def test_compress_pdf_invalid_mode(tmp_path: Path):
    source = _make_pdf(tmp_path / "bad.pdf")
    output = tmp_path / "bad_out.pdf"

    config = CompressionConfig(pdf_mode="invalid", output_dir=tmp_path)
    with pytest.raises(ValueError, match="pdf_mode"):
        compress_pdf(source, output, config)


def test_compress_pdf_with_target_size(tmp_path: Path):
    source = _make_pdf(tmp_path / "target.pdf", pages=5, with_images=True)
    output = tmp_path / "target_out.pdf"

    config = CompressionConfig(
        target_bytes=50_000,
        pdf_mode="raster",
        pdf_dpi=100,
        quality=50,
        output_dir=tmp_path,
    )
    compress_pdf(source, output, config)

    assert output.exists()
    assert output.stat().st_size <= 60_000


def test_compress_pdf_auto_with_target_falls_back_to_raster(tmp_path: Path):
    source = _make_pdf(tmp_path / "fallback.pdf", pages=5, with_images=True)
    output = tmp_path / "fallback_out.pdf"

    config = CompressionConfig(
        target_bytes=30_000,
        pdf_mode="auto",
        pdf_dpi=100,
        quality=50,
        output_dir=tmp_path,
    )
    compress_pdf(source, output, config)

    assert output.exists()


def test_compress_pdf_auto_with_large_target_returns_optimized(tmp_path: Path):
    """Auto mode returns optimized result when it's within ±10% of target."""
    source = _make_pdf(tmp_path / "big_target.pdf", pages=3)
    output = tmp_path / "big_target_out.pdf"
    opt_size = optimize_pdf(source, tmp_path / "opt.pdf", CompressionConfig(output_dir=tmp_path)).stat().st_size

    config = CompressionConfig(
        target_bytes=int(opt_size * 1.05),
        pdf_mode="auto",
        pdf_dpi=120,
        quality=82,
        output_dir=tmp_path,
    )
    compress_pdf(source, output, config)
    assert output.exists()
    assert output.stat().st_size == opt_size


def test_compress_pdf_grayscale_with_target(tmp_path: Path):
    source = _make_pdf(tmp_path / "gray.pdf", pages=3, with_images=True)
    output = tmp_path / "gray_out.pdf"

    config = CompressionConfig(
        target_bytes=50_000,
        pdf_mode="raster",
        pdf_dpi=80,
        quality=40,
        pdf_grayscale=True,
        output_dir=tmp_path,
    )
    compress_pdf(source, output, config)

    assert output.exists()
    assert output.stat().st_size <= 60_000


def test_compress_pdf_keep_metadata(tmp_path: Path):
    source = _make_pdf(tmp_path / "meta.pdf")
    output = tmp_path / "meta_out.pdf"

    config = CompressionConfig(pdf_mode="optimize", strip_metadata=False, output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()


def test_fitz_import_error(tmp_path: Path):
    import builtins
    import sys
    from unittest.mock import patch
    from file_compressor.pdfs import _fitz

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No module named 'fitz'")
        return real_import(name, *args, **kwargs)

    saved = sys.modules.pop("fitz", None)
    try:
        with patch.object(builtins, "__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="PyMuPDF"):
                _fitz()
    finally:
        if saved is not None:
            sys.modules["fitz"] = saved
