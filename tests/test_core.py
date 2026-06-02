from __future__ import annotations

from pathlib import Path

import pytest

from file_compressor.core import compress_path, _archive_quality_candidates, _archive_attempt_config
from file_compressor.models import CompressionConfig

from conftest import make_test_pdf


def _make_pdf(path: Path, pages: int = 2) -> Path:
    return make_test_pdf(path, pages)


def _make_image(path: Path, size: tuple[int, int] = (200, 200)) -> Path:
    from PIL import Image
    img = Image.new("RGB", size, color="red")
    img.save(path)
    return path


# ── compress_path with PDF ──

def test_compress_path_pdf(tmp_path: Path):
    source = _make_pdf(tmp_path / "input.pdf")
    config = CompressionConfig(output_dir=tmp_path)
    summary = compress_path(source, config)
    assert len(summary.results) == 1
    assert summary.results[0].status == "ok"
    assert summary.results[0].output.exists()


def test_compress_path_pdf_with_output(tmp_path: Path):
    source = _make_pdf(tmp_path / "in.pdf")
    output = tmp_path / "custom" / "out.pdf"
    config = CompressionConfig(output_dir=tmp_path)
    summary = compress_path(source, config, output)
    assert summary.results[0].output == output


def test_compress_path_image(tmp_path: Path):
    source = _make_image(tmp_path / "photo.jpg")
    config = CompressionConfig(output_dir=tmp_path)
    summary = compress_path(source, config)
    assert len(summary.results) == 1
    assert summary.results[0].status == "ok"


def test_compress_path_directory(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    _make_pdf(docs / "a.pdf")
    _make_image(docs / "b.jpg")
    config = CompressionConfig(output_dir=tmp_path / "out")
    summary = compress_path(tmp_path / "docs", config)
    assert len(summary.results) == 2
    assert all(r.status == "ok" for r in summary.results)


def test_compress_path_nonexistent_raises(tmp_path: Path):
    config = CompressionConfig(output_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        compress_path(tmp_path / "nope.pdf", config)


def test_compress_path_unsupported_file_skipped_in_directory(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    _make_pdf(docs / "good.pdf")
    (docs / "bad.xyz").write_text("hello")
    config = CompressionConfig(output_dir=tmp_path / "out")
    summary = compress_path(docs, config)
    assert len(summary.results) == 1
    assert summary.results[0].status == "ok"


def test_compress_path_target_size_pdf(tmp_path: Path):
    source = _make_pdf(tmp_path / "big.pdf", pages=5)
    config = CompressionConfig(target_bytes=50_000, pdf_mode="raster", pdf_dpi=80, quality=40, output_dir=tmp_path)
    summary = compress_path(source, config)
    assert summary.results[0].status == "ok"


# ── compress_path with archive ──

def test_compress_path_zip(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    _make_pdf(docs / "a.pdf")
    _make_image(docs / "b.jpg")
    config = CompressionConfig(archive="zip", output_dir=tmp_path)
    summary = compress_path(docs, config)
    assert summary.archive is not None
    assert summary.archive.output.exists()
    assert summary.archive.output.suffix == ".zip"


def test_compress_path_zip_single_file(tmp_path: Path):
    source = _make_pdf(tmp_path / "single.pdf")
    config = CompressionConfig(archive="zip", output_dir=tmp_path)
    summary = compress_path(source, config)
    assert summary.archive is not None


def test_compress_path_invalid_archive(tmp_path: Path):
    source = _make_pdf(tmp_path / "x.pdf")
    config = CompressionConfig(archive="tar", output_dir=tmp_path)
    with pytest.raises(ValueError, match="archive must be zip"):
        compress_path(source, config)


# ── _archive_quality_candidates ──

def test_archive_quality_candidates_no_target():
    assert _archive_quality_candidates(82, None) == [82]


def test_archive_quality_candidates_with_target():
    candidates = _archive_quality_candidates(82, 500_000)
    assert candidates[0] == 82
    assert 20 in candidates
    assert all(q >= 20 for q in candidates)
    assert len(candidates) > 1


def test_archive_quality_candidates_low_start():
    candidates = _archive_quality_candidates(30, 100_000)
    assert candidates[0] == 30
    assert candidates[-1] == 20


def test_archive_quality_candidates_clamped():
    candidates = _archive_quality_candidates(100, 500_000)
    assert candidates[0] == 95


def test_archive_quality_candidates_low_quality_with_target():
    candidates = _archive_quality_candidates(1, 50_000)
    assert candidates == [1]


def test_archive_quality_candidates_boundary_20():
    candidates = _archive_quality_candidates(20, 50_000)
    assert candidates == [20]


def test_archive_quality_candidates_just_above_20():
    candidates = _archive_quality_candidates(21, 50_000)
    assert candidates[0] == 21
    assert 20 in candidates


def test_compress_path_zip_with_target(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    _make_pdf(docs / "a.pdf")
    _make_image(docs / "b.jpg")
    config = CompressionConfig(archive="zip", target_bytes=10_000, output_dir=tmp_path)
    summary = compress_path(docs, config)
    assert summary.archive is not None


def test_compress_path_zip_fallback_best(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    _make_pdf(docs / "a.pdf")
    _make_image(docs / "b.jpg")
    config = CompressionConfig(archive="zip", target_bytes=1, output_dir=tmp_path)
    summary = compress_path(docs, config)
    assert summary.archive is not None
    assert summary.archive.status == "best_over_target"


def test_compress_unsupported_file_type(tmp_path: Path):
    source = tmp_path / "file.xyz"
    source.write_text("hello")
    output = tmp_path / "out.xyz"
    config = CompressionConfig(output_dir=tmp_path)
    summary = compress_path(source, config, output)
    assert summary.results[0].status == "failed"
    assert "Unsupported" in (summary.results[0].error or "")


def test_compress_relative_output_dir(tmp_path: Path):
    source = _make_pdf(tmp_path / "input.pdf")
    import os
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        config = CompressionConfig(output_dir=Path("relative_out"))
        summary = compress_path(source, config)
        assert summary.results[0].status == "ok"
        assert summary.results[0].output.exists()
    finally:
        os.chdir(old_cwd)


def test_compress_path_zip_relative_output(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    _make_pdf(docs / "a.pdf")
    import os
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        config = CompressionConfig(archive="zip", output_dir=Path("."))
        summary = compress_path(docs, config, Path("rel_archive.zip"))
        assert summary.archive is not None
        assert summary.archive.output.exists()
    finally:
        os.chdir(old_cwd)


def test_compress_path_output_as_directory(tmp_path: Path):
    source = _make_pdf(tmp_path / "input.pdf")
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    config = CompressionConfig(output_dir=tmp_path)
    summary = compress_path(source, config, out_dir)
    assert summary.results[0].status == "ok"
    assert summary.results[0].output.exists()
    assert summary.results[0].output.parent == out_dir


def test_compress_path_overwrite(tmp_path: Path):
    source = _make_pdf(tmp_path / "in.pdf")
    output = tmp_path / "out.pdf"
    output.write_bytes(b"existing")
    config = CompressionConfig(output_dir=tmp_path, overwrite=True)
    summary = compress_path(source, config, output)
    assert summary.results[0].status == "ok"
    assert summary.results[0].output.exists()


def test_compress_path_no_overwrite_creates_unique(tmp_path: Path):
    source = _make_pdf(tmp_path / "in.pdf")
    output = tmp_path / "out.pdf"
    output.write_bytes(b"existing")
    config = CompressionConfig(output_dir=tmp_path, overwrite=False)
    summary = compress_path(source, config, output)
    assert summary.results[0].status == "ok"
    assert summary.results[0].output != output
    assert summary.results[0].output.name == "out_1.pdf"


# ── _archive_attempt_config ──

def test_archive_attempt_config_edge_decreases_without_user_edge():
    config = CompressionConfig(target_bytes=50_000)
    e0 = _archive_attempt_config(config, 82, 0).max_edge
    e1 = _archive_attempt_config(config, 82, 1).max_edge
    e2 = _archive_attempt_config(config, 82, 2).max_edge
    assert e0 is None
    assert e1 is not None and e2 is not None
    assert e1 > e2


def test_archive_attempt_config_respects_user_max_edge():
    config = CompressionConfig(max_edge=500, target_bytes=50_000)
    for attempt in range(4):
        result = _archive_attempt_config(config, 82, attempt)
        assert result.max_edge == 500


def test_archive_attempt_config_large_user_edge_decreases():
    config = CompressionConfig(max_edge=3000, target_bytes=50_000)
    e0 = _archive_attempt_config(config, 82, 0).max_edge
    e1 = _archive_attempt_config(config, 82, 1).max_edge
    assert e0 == 3000
    assert e1 < 3000


def test_archive_attempt_config_dpi_decreases():
    config = CompressionConfig(pdf_dpi=200, target_bytes=50_000)
    d0 = _archive_attempt_config(config, 82, 0).pdf_dpi
    d1 = _archive_attempt_config(config, 82, 1).pdf_dpi
    assert d0 > d1
