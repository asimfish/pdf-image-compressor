from pathlib import Path

from PIL import Image

from file_compressor.core import compress_path
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
