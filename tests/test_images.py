from pathlib import Path

from PIL import Image

from file_compressor.core import compress_path
from file_compressor.images import compress_image
from file_compressor.models import CompressionConfig


def test_compress_image_to_output_dir(tmp_path: Path):
    source = tmp_path / "input.jpg"
    Image.new("RGB", (1200, 900), "red").save(source, quality=95)

    summary = compress_path(source, CompressionConfig(output_dir=tmp_path / "compressed", quality=50, max_edge=400))

    assert len(summary.results) == 1
    result = summary.results[0]
    assert result.status == "ok"
    assert result.output is not None
    assert result.output.exists()
    assert result.compressed_size is not None
    assert result.compressed_size < result.original_size


def test_compress_image_to_webp(tmp_path: Path):
    source = tmp_path / "input.png"
    Image.new("RGB", (800, 600), "blue").save(source)

    summary = compress_path(source, CompressionConfig(output_dir=tmp_path / "compressed", to_webp=True, quality=70))

    result = summary.results[0]
    assert result.output is not None
    assert result.output.suffix == ".webp"
    assert result.output.exists()


def test_compress_image_with_target_size(tmp_path: Path):
    source = tmp_path / "big.jpg"
    Image.new("RGB", (2000, 1500), "green").save(source, quality=95)
    output = tmp_path / "small.jpg"

    config = CompressionConfig(target_bytes=50_000, output_dir=tmp_path)
    compress_image(source, output, config)

    assert output.exists()
    assert output.stat().st_size <= 60_000


def test_compress_image_bmp_to_jpg(tmp_path: Path):
    source = tmp_path / "input.bmp"
    Image.new("RGB", (400, 300), "yellow").save(source, format="BMP")

    summary = compress_path(source, CompressionConfig(output_dir=tmp_path / "compressed", quality=70))

    result = summary.results[0]
    assert result.output is not None
    assert result.output.suffix == ".jpg"


def test_compress_png(tmp_path: Path):
    source = tmp_path / "input.png"
    Image.new("RGBA", (800, 600), (255, 0, 0, 128)).save(source)

    summary = compress_path(source, CompressionConfig(output_dir=tmp_path / "compressed", quality=50))

    result = summary.results[0]
    assert result.status == "ok"
    assert result.output is not None
    assert result.output.suffix == ".png"
    assert result.compressed_size is not None
    assert result.compressed_size <= result.original_size


def test_compress_png_low_quality_quantizes(tmp_path: Path):
    source = tmp_path / "big.png"
    Image.new("RGB", (1200, 900), "blue").save(source)

    config = CompressionConfig(output_dir=tmp_path / "compressed", quality=30)
    summary = compress_path(source, config)

    result = summary.results[0]
    assert result.status == "ok"
    assert result.compressed_size is not None


def test_compress_rgba_to_jpg(tmp_path: Path):
    source = tmp_path / "rgba.png"
    Image.new("RGBA", (400, 300), (255, 0, 0, 128)).save(source)
    output = tmp_path / "out.jpg"

    config = CompressionConfig(output_dir=tmp_path, to_webp=False)
    compress_image(source, output, config)

    assert output.exists()
    assert output.suffix == ".jpg"


def test_compress_palette_transparency_to_jpg(tmp_path: Path):
    source = tmp_path / "pal.png"
    img = Image.new("P", (100, 100))
    img.info["transparency"] = 0
    img.save(source)
    output = tmp_path / "out.jpg"

    config = CompressionConfig(output_dir=tmp_path)
    compress_image(source, output, config)
    assert output.exists()


def test_compress_grayscale_to_jpg(tmp_path: Path):
    source = tmp_path / "gray.jpg"
    Image.new("L", (200, 200), 128).save(source)
    output = tmp_path / "out.jpg"

    config = CompressionConfig(output_dir=tmp_path)
    compress_image(source, output, config)
    assert output.exists()


def test_compress_grayscale_to_webp(tmp_path: Path):
    source = tmp_path / "gray.png"
    Image.new("L", (200, 200), 128).save(source)
    output = tmp_path / "out.webp"

    config = CompressionConfig(output_dir=tmp_path, to_webp=True)
    compress_image(source, output, config)
    assert output.exists()


def test_compress_unsupported_image_raises(tmp_path: Path):
    source = tmp_path / "bad.jpg"
    source.write_text("not an image")
    output = tmp_path / "out.jpg"

    config = CompressionConfig(output_dir=tmp_path)
    import pytest
    with pytest.raises(RuntimeError, match="Unsupported or corrupt"):
        compress_image(source, output, config)


def test_edge_candidates_with_max_edge():
    from file_compressor.images import _edge_candidates
    result = _edge_candidates(2000)
    assert result[0] == 2000
    assert all(e >= 320 for e in result)
    assert len(result) == 5


def test_quality_candidates_low_quality():
    from file_compressor.images import _quality_candidates
    result = _quality_candidates(1)
    assert result == [1]


def test_quality_candidates_boundary_15():
    from file_compressor.images import _quality_candidates
    result = _quality_candidates(15)
    assert result == [15]


def test_quality_candidates_just_above_15():
    from file_compressor.images import _quality_candidates
    result = _quality_candidates(16)
    assert result[0] == 16
    assert 15 in result


def test_edge_candidates_without_max_edge():
    from file_compressor.images import _edge_candidates
    result = _edge_candidates(None)
    assert result[0] is None
    assert 2400 in result
    assert len(result) == 10


def test_compress_fallback_format(tmp_path: Path):
    source = tmp_path / "input.tiff"
    Image.new("RGB", (200, 200), "red").save(source, format="TIFF")
    output = tmp_path / "out.jpg"

    config = CompressionConfig(output_dir=tmp_path)
    compress_image(source, output, config)
    assert output.exists()


def test_compress_image_low_quality_with_target(tmp_path: Path):
    source = tmp_path / "big.jpg"
    Image.new("RGB", (2000, 1500), "green").save(source, quality=95)
    output = tmp_path / "small.jpg"

    config = CompressionConfig(target_bytes=50_000, quality=1, output_dir=tmp_path)
    compress_image(source, output, config)

    assert output.exists()
