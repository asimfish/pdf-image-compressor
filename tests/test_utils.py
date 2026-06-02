from pathlib import Path

import pytest

from file_compressor.utils import (
    format_size,
    is_image,
    is_pdf,
    is_supported,
    iter_supported_files,
    parse_size,
    unique_path,
)


# ── parse_size ──

def test_parse_size_plain_bytes():
    assert parse_size("500") == 500


def test_parse_size_kb():
    assert parse_size("500KB") == 500_000


def test_parse_size_mb_decimal():
    assert parse_size("1.5MB") == 1_500_000


def test_parse_size_none():
    assert parse_size(None) is None


def test_parse_size_empty():
    assert parse_size("") is None


def test_parse_size_zero():
    assert parse_size("0") is None


def test_parse_size_zero_mb():
    assert parse_size("0MB") is None


def test_parse_size_gb():
    assert parse_size("2GB") == 2_000_000_000


def test_parse_size_tb():
    assert parse_size("1TB") == 1_000_000_000_000


def test_parse_size_with_spaces():
    assert parse_size(" 500 KB ") == 500_000


def test_parse_size_invalid():
    with pytest.raises(ValueError):
        parse_size("abc")


# ── format_size ──

def test_format_size_bytes():
    assert format_size(500) == "500 B"


def test_format_size_kb():
    assert format_size(50_000) == "50.0 KB"


def test_format_size_mb():
    assert format_size(5_000_000) == "5.0 MB"


def test_format_size_gb():
    assert format_size(2_000_000_000) == "2.0 GB"


def test_format_size_tb():
    assert format_size(1_000_000_000_000) == "1.0 TB"


def test_format_size_none():
    assert format_size(None) == "-"


# ── unique_path ──

def test_unique_path_no_conflict(tmp_path: Path):
    target = tmp_path / "new.pdf"
    assert unique_path(target, overwrite=False) == target


def test_unique_path_overwrite(tmp_path: Path):
    target = tmp_path / "existing.pdf"
    target.write_bytes(b"data")
    assert unique_path(target, overwrite=True) == target


def test_unique_path_conflict(tmp_path: Path):
    target = tmp_path / "file.pdf"
    target.write_bytes(b"original")
    result = unique_path(target, overwrite=False)
    assert result != target
    assert result.name == "file_1.pdf"
    assert result.parent == tmp_path


# ── is_image / is_pdf / is_supported ──

def test_is_image():
    assert is_image(Path("photo.jpg"))
    assert is_image(Path("photo.jpeg"))
    assert is_image(Path("icon.png"))
    assert is_image(Path("anim.webp"))
    assert is_image(Path("scan.bmp"))
    assert is_image(Path("page.tif"))
    assert is_image(Path("page.tiff"))
    assert not is_image(Path("doc.pdf"))
    assert not is_image(Path("file.txt"))


def test_is_pdf():
    assert is_pdf(Path("report.pdf"))
    assert is_pdf(Path("REPORT.PDF"))
    assert not is_pdf(Path("image.jpg"))
    assert not is_pdf(Path("file.txt"))


def test_is_supported(tmp_path: Path):
    jpg = tmp_path / "a.jpg"
    jpg.write_bytes(b"data")
    assert is_supported(jpg)
    assert not is_supported(tmp_path / "a.txt")
    assert not is_supported(tmp_path / "nonexistent.pdf")


# ── iter_supported_files ──

def test_iter_supported_files_single(tmp_path: Path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    result = list(iter_supported_files(pdf))
    assert result == [pdf]


def test_iter_supported_files_dir(tmp_path: Path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "b.jpg").write_bytes(b"data")
    (tmp_path / "c.txt").write_bytes(b"skip")
    result = list(iter_supported_files(tmp_path))
    names = {p.name for p in result}
    assert "a.pdf" in names
    assert "b.jpg" in names
    assert "c.txt" not in names


def test_iter_supported_files_unsupported_single(tmp_path: Path):
    txt = tmp_path / "readme.txt"
    txt.write_bytes(b"data")
    result = list(iter_supported_files(txt))
    assert result == []
