from pathlib import Path

import pytest

from file_compressor.utils import format_size, parse_size, unique_path


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


def test_parse_size_gb():
    assert parse_size("2GB") == 2_000_000_000


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
