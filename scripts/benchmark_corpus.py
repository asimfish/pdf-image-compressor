# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Head-to-head compression benchmark over a user-supplied PDF corpus.

Every document in the manifest is compressed by PaperSqueeze (and optionally by
PixShift) to the *same* byte budget, then scored with the tool-neutral
``scripts/compare_pdf_quality.py`` (RGB SSIM per page, text / link / soft-mask
fingerprints) plus wall-clock runtime. Results are written as JSON and printed
as a Markdown table.

Manifest format (JSON list)::

    [
      {"name": "study_notes", "path": "corpus/study_notes.pdf", "budget": "10MiB"},
      {"name": "nature", "path": "corpus/nature.pdf", "budget": 3145728}
    ]

``budget`` accepts a byte count or a size string. ``KiB``/``MiB``/``GiB`` are
binary, ``KB``/``MB``/``GB`` are decimal (matching the CLI's ``--target-size``).

Usage::

    uv run scripts/benchmark_corpus.py --manifest corpus.json --out bench/
    uv run scripts/benchmark_corpus.py --manifest corpus.json --out bench/ --pixshift

The corpus itself is not part of the repository: use your own papers, scans
and slide decks. Keep sources on a plain local path (not inside another app's
sandboxed container) so both tools can read them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
COMPARE = REPO / "scripts" / "compare_pdf_quality.py"

_BUDGET_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$")
_UNITS = {
    "": 1,
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
}


def parse_budget(value: Any) -> int:
    """Return a byte count for an int or a size string such as ``10MiB``."""
    if isinstance(value, bool):
        raise ValueError("budget must be a number or size string")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("budget must be positive")
        return value
    match = _BUDGET_RE.match(str(value))
    if not match:
        raise ValueError(f"invalid budget: {value!r}")
    number, unit = float(match.group(1)), match.group(2).upper()
    if unit not in _UNITS:
        raise ValueError(f"unknown unit in budget: {value!r}")
    result = int(number * _UNITS[unit])
    if result <= 0:
        raise ValueError("budget must be positive")
    return result


def load_manifest(path: Path) -> list[dict[str, Any]]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest must be a non-empty JSON list")
    corpus = []
    for raw in entries:
        name = str(raw["name"])
        source = Path(raw["path"])
        if not source.is_absolute():
            source = (path.parent / source).resolve()
        corpus.append({"name": name, "path": source, "budget": parse_budget(raw["budget"])})
    return corpus


def _run(cmd: list[str], cwd: Optional[Path] = None) -> tuple[float, str, int]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return time.perf_counter() - started, proc.stdout + proc.stderr, proc.returncode


def score(source: Path, candidate: Path, dpi: int) -> dict[str, Any]:
    _, text, code = _run(
        ["uv", "run", str(COMPARE), str(source), str(candidate), "--json", "--dpi", str(dpi)],
        cwd=REPO,
    )
    line = next((ln for ln in text.splitlines() if ln.startswith("{")), None)
    if code != 0 or line is None:
        return {"score_error": text.strip()[-400:]}
    data = json.loads(line)
    cand = data["comparisons"]["candidate"]
    original, result = data["documents"]["original"], data["documents"]["candidate"]
    return {
        "size": result["size_bytes"],
        "ssim_mean": cand["ssim_mean"],
        "ssim_min": cand["ssim_min"],
        "ssim_min_page": cand["ssim_min_page"],
        "text_ok": result["text_sha256"] == original["text_sha256"],
        "links_ok": result["links_sha256"] == original["links_sha256"],
        "smask_ok": result["soft_mask_images_sha256"] == original["soft_mask_images_sha256"],
        "pages_ok": result["page_count"] == original["page_count"],
    }


def run_papersqueeze(source: Path, budget: int, mode: str, out_dir: Path, dpi: int) -> dict[str, Any]:
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    elapsed, log, code = _run(
        [
            "uv", "run", "file-compressor", "compress", str(source),
            "--target-size", str(budget), "--pdf-mode", mode, "--output-dir", str(out_dir),
        ],
        cwd=REPO,
    )
    record: dict[str, Any] = {"time": round(elapsed, 1), "exit": code, "log": log.strip()[-300:]}
    produced = out_dir / source.name
    if produced.exists():
        record.update(score(source, produced, dpi))
    return record


def run_pixshift(source: Path, budget: int, out_pdf: Path, dpi: int) -> dict[str, Any]:
    if out_pdf.exists():
        out_pdf.unlink()
    elapsed, log, code = _run(
        [
            "uvx", "pixshift", "pdf", "compress", str(source),
            "--target-size", f"{budget}B", "-o", str(out_pdf), "--json",
        ]
    )
    record: dict[str, Any] = {"time": round(elapsed, 1), "exit": code}
    try:
        payload = json.loads(next(ln for ln in log.splitlines() if ln.startswith("{")))
        record["log"] = payload.get("error", "") or payload.get("details", {}).get("strategy", "")
    except (StopIteration, json.JSONDecodeError):
        record["log"] = log.strip()[-300:]
    if out_pdf.exists():
        record.update(score(source, out_pdf, dpi))
    return record


def _cell(record: Optional[dict[str, Any]], budget: int) -> str:
    if record is None:
        return "not run"
    if "size" not in record:
        reason = record.get("log") or record.get("score_error") or "failed"
        return f"FAILED ({str(reason)[:40]}) {record.get('time', 0):.0f}s"
    fits = "" if record["size"] <= budget else " (over)"
    return (
        f"{record['size']:,} B ({record['size'] / budget * 100:.1f}%){fits} - "
        f"SSIM {record['ssim_mean']:.4f}/{record['ssim_min']:.4f} - {record['time']:.0f}s"
    )


def format_table(results: dict[str, dict[str, Any]], ours_key: str, with_pixshift: bool) -> str:
    header = ["Document", "Source", "Budget", "PaperSqueeze (size / SSIM mean/min / time)"]
    if with_pixshift:
        header.append("PixShift (size / SSIM mean/min / time)")
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    fits = {"ours": 0, "pix": 0}
    total_time = {"ours": 0.0, "pix": 0.0}
    for name, entry in results.items():
        budget = entry["budget"]
        ours = entry.get(ours_key)
        row = [name, f"{entry['src_size'] / 1e6:.1f} MB", f"{budget:,} B", _cell(ours, budget)]
        if ours and "size" in ours:
            fits["ours"] += ours["size"] <= budget
            total_time["ours"] += ours["time"]
        if with_pixshift:
            pix = entry.get("pixshift")
            row.append(_cell(pix, budget))
            if pix and "size" in pix:
                fits["pix"] += pix["size"] <= budget
            total_time["pix"] += (pix or {}).get("time", 0)
        lines.append("| " + " | ".join(row) + " |")
    count = len(results)
    summary = f"\nBudget met: PaperSqueeze {fits['ours']}/{count}"
    if with_pixshift:
        summary += f", PixShift {fits['pix']}/{count}"
    summary += f". Total time: PaperSqueeze {total_time['ours']:.0f}s"
    if with_pixshift:
        summary += f", PixShift {total_time['pix']:.0f}s"
    return "\n".join(lines) + summary + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="directory for outputs and results.json")
    parser.add_argument("--mode", default="auto", choices=["fidelity", "auto", "text"])
    parser.add_argument("--pixshift", action="store_true", help="also run PixShift via uvx")
    parser.add_argument("--dpi", type=int, default=96, help="render DPI for SSIM scoring")
    parser.add_argument("--only", nargs="*", default=None, help="restrict to these manifest names")
    args = parser.parse_args(argv)

    corpus = load_manifest(args.manifest)
    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "results.json"
    results: dict[str, dict[str, Any]] = (
        json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {}
    )
    ours_key = f"papersqueeze_{args.mode}"
    for item in corpus:
        name, source, budget = item["name"], item["path"], item["budget"]
        if args.only and name not in args.only:
            continue
        if not source.exists():
            print(f"[{name}] missing source {source}, skipping", flush=True)
            continue
        entry = results.setdefault(
            name, {"src": str(source), "src_size": source.stat().st_size, "budget": budget}
        )
        print(f"[{name}] PaperSqueeze ({args.mode}) ...", flush=True)
        entry[ours_key] = run_papersqueeze(source, budget, args.mode, args.out / name / ours_key, args.dpi)
        print(f"[{name}] -> {_cell(entry[ours_key], budget)}", flush=True)
        results_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
        if args.pixshift:
            previous = entry.get("pixshift")
            pix_out = args.out / name / "pixshift.pdf"
            if previous and "size" in previous and pix_out.exists():
                previous.update(score(source, pix_out, args.dpi))
                print(f"[{name}] PixShift re-scored", flush=True)
            elif previous and "size" not in previous and previous.get("exit") is not None:
                print(f"[{name}] PixShift previously failed ({previous.get('log')}), skipping", flush=True)
            else:
                print(f"[{name}] PixShift ...", flush=True)
                entry["pixshift"] = run_pixshift(source, budget, pix_out, args.dpi)
                print(f"[{name}] -> {_cell(entry['pixshift'], budget)}", flush=True)
            results_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print()
    print(format_table(results, ours_key, args.pixshift))
    return 0


if __name__ == "__main__":
    os.chdir(REPO)
    sys.exit(main())
