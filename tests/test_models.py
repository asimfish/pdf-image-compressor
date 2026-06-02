from pathlib import Path

from file_compressor.models import CompressionConfig, CompressionResult, CompressionSummary


# ── CompressionConfig ──

def test_with_quality_clamps_low():
    config = CompressionConfig(quality=82)
    result = config.with_quality(0)
    assert result.quality == 1


def test_with_quality_clamps_high():
    config = CompressionConfig(quality=82)
    result = config.with_quality(100)
    assert result.quality == 95


def test_with_quality_normal():
    config = CompressionConfig(quality=82)
    result = config.with_quality(50)
    assert result.quality == 50


def test_with_target():
    config = CompressionConfig()
    result = config.with_target(500_000)
    assert result.target_bytes == 500_000


def test_with_target_none():
    config = CompressionConfig(target_bytes=500_000)
    result = config.with_target(None)
    assert result.target_bytes is None


def test_config_immutable():
    config = CompressionConfig(quality=82)
    config.with_quality(50)
    assert config.quality == 82


# ── CompressionResult ──

def test_saved_bytes():
    result = CompressionResult(
        source=Path("a.pdf"), output=Path("b.pdf"),
        original_size=1000, compressed_size=400, status="ok",
    )
    assert result.saved_bytes == 600


def test_saved_bytes_none_when_no_compressed():
    result = CompressionResult(
        source=Path("a.pdf"), output=None,
        original_size=1000, compressed_size=None, status="failed",
    )
    assert result.saved_bytes is None


def test_compression_ratio():
    result = CompressionResult(
        source=Path("a.pdf"), output=Path("b.pdf"),
        original_size=1000, compressed_size=400, status="ok",
    )
    assert result.compression_ratio == 0.6


def test_compression_ratio_none_when_no_compressed():
    result = CompressionResult(
        source=Path("a.pdf"), output=None,
        original_size=1000, compressed_size=None, status="failed",
    )
    assert result.compression_ratio is None


def test_compression_ratio_none_when_zero_original():
    result = CompressionResult(
        source=Path("a.pdf"), output=Path("b.pdf"),
        original_size=0, compressed_size=0, status="ok",
    )
    assert result.compression_ratio is None


# ── CompressionSummary ──

def test_all_results_without_archive():
    r1 = CompressionResult(source=Path("a.pdf"), output=Path("a.pdf"), original_size=100, compressed_size=50, status="ok")
    summary = CompressionSummary(results=[r1])
    assert len(summary.all_results) == 1
    assert summary.all_results[0] is r1


def test_all_results_with_archive():
    r1 = CompressionResult(source=Path("a.pdf"), output=Path("a.pdf"), original_size=100, compressed_size=50, status="ok")
    archive = CompressionResult(source=Path("dir"), output=Path("dir.zip"), original_size=100, compressed_size=40, status="ok")
    summary = CompressionSummary(results=[r1], archive=archive)
    assert len(summary.all_results) == 2
    assert summary.all_results[1] is archive


def test_result_immutable():
    import pytest
    result = CompressionResult(source=Path("a.pdf"), output=Path("b.pdf"), original_size=100, compressed_size=50, status="ok")
    with pytest.raises(AttributeError):
        result.status = "failed"
