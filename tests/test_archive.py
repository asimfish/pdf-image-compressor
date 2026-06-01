from pathlib import Path
from zipfile import ZipFile

from PIL import Image

from file_compressor.core import compress_path
from file_compressor.models import CompressionConfig


def test_compress_directory_to_zip(tmp_path: Path):
    source_dir = tmp_path / "files"
    source_dir.mkdir()
    Image.new("RGB", (500, 500), "green").save(source_dir / "a.jpg", quality=95)
    Image.new("RGB", (500, 500), "yellow").save(source_dir / "b.png")
    output = tmp_path / "out.zip"

    summary = compress_path(source_dir, CompressionConfig(archive="zip", quality=60, output_dir=tmp_path / "compressed"), output)

    assert summary.archive is not None
    assert summary.archive.output == output
    assert output.exists()
    with ZipFile(output) as archive:
        names = set(archive.namelist())
    assert names
    assert any(name.endswith("a.jpg") for name in names)


def test_compress_single_file_to_zip(tmp_path: Path):
    source = tmp_path / "single.jpg"
    Image.new("RGB", (500, 500), "purple").save(source, quality=95)
    output = tmp_path / "single.zip"

    summary = compress_path(source, CompressionConfig(archive="zip", quality=60), output)

    assert summary.archive is not None
    assert output.exists()
    with ZipFile(output) as archive:
        assert archive.namelist()
