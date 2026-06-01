from file_compressor.utils import parse_size


def test_parse_size_plain_bytes():
    assert parse_size("500") == 500


def test_parse_size_kb():
    assert parse_size("500KB") == 500_000


def test_parse_size_mb_decimal():
    assert parse_size("1.5MB") == 1_500_000
