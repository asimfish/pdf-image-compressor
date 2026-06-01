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
