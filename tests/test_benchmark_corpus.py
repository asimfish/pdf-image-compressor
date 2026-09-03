import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "benchmark_corpus", Path(__file__).resolve().parents[1] / "scripts" / "benchmark_corpus.py"
)
bench = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(bench)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (8388608, 8388608),
        ("10MiB", 10 * 1024 * 1024),
        ("10MB", 10_000_000),
        ("512KiB", 512 * 1024),
        ("1.5GB", 1_500_000_000),
        ("2048", 2048),
        ("2048B", 2048),
    ],
)
def test_parse_budget_accepts_binary_and_decimal_units(value, expected):
    assert bench.parse_budget(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "10 parsecs", "-5", 0, True])
def test_parse_budget_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        bench.parse_budget(value)


def test_load_manifest_resolves_relative_paths(tmp_path: Path):
    manifest = tmp_path / "corpus.json"
    manifest.write_text(
        json.dumps(
            [
                {"name": "a", "path": "pdfs/a.pdf", "budget": "3MiB"},
                {"name": "b", "path": str(tmp_path / "b.pdf"), "budget": 1000},
            ]
        )
    )
    corpus = bench.load_manifest(manifest)
    assert corpus[0]["path"] == (tmp_path / "pdfs" / "a.pdf").resolve()
    assert corpus[0]["budget"] == 3 * 1024 * 1024
    assert corpus[1]["path"] == tmp_path / "b.pdf" and corpus[1]["budget"] == 1000


def test_load_manifest_rejects_empty(tmp_path: Path):
    manifest = tmp_path / "empty.json"
    manifest.write_text("[]")
    with pytest.raises(ValueError):
        bench.load_manifest(manifest)


def test_format_table_reports_fit_counts_and_failures():
    results = {
        "paper": {
            "src_size": 28_900_000,
            "budget": 6_291_456,
            "papersqueeze_auto": {"size": 6_273_262, "ssim_mean": 0.9775, "ssim_min": 0.892, "time": 15.0},
            "pixshift": {"exit": 1, "time": 208.0, "log": "target_size_unreachable"},
        },
        "handbook": {
            "src_size": 26_600_000,
            "budget": 8_388_608,
            "papersqueeze_auto": {"size": 13_529_697, "ssim_mean": 0.9979, "ssim_min": 0.9804, "time": 39.0},
            "pixshift": {"size": 8_300_000, "ssim_mean": 0.99, "ssim_min": 0.98, "time": 900.0},
        },
    }
    table = bench.format_table(results, "papersqueeze_auto", with_pixshift=True)
    assert "| paper |" in table and "FAILED (target_size_unreachable)" in table
    assert "(over)" in table  # handbook exceeds its budget
    assert "Budget met: PaperSqueeze 1/2, PixShift 1/2" in table
    assert "Total time: PaperSqueeze 54s, PixShift 1108s" in table

    ours_only = bench.format_table(results, "papersqueeze_auto", with_pixshift=False)
    assert "PixShift" not in ours_only
