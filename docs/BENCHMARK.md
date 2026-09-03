# Benchmark: PaperSqueeze vs PixShift

English | [中文](BENCHMARK_CN.md)

This document records the head-to-head comparison behind the numbers quoted in
the README, so that every claim can be checked and re-run.

## Setup

| Item | Value |
| --- | --- |
| PaperSqueeze | commit `3a30a9c` (main, 2026-09-03), `--pdf-mode auto` |
| PixShift | 2.0.0 via `uvx pixshift pdf compress --target-size <bytes>B` |
| Machine | Apple silicon MacBook, 12 CPU threads, macOS 15 |
| Runtime | Python 3.13, PyMuPDF 1.28, Pillow 11 |
| Budget | identical byte count for both tools (MiB budgets, passed as plain bytes) |
| Scoring | `scripts/compare_pdf_quality.py`: per-page RGB multichannel SSIM at 96 DPI, text / link / soft-mask SHA-256 fingerprints, page count and geometry |
| Timing | wall-clock of the CLI process, single run, machine otherwise idle |

Both tools were driven by `scripts/benchmark_corpus.py`. PixShift was left at
its defaults; it never downscales in target mode and refuses to touch images
that carry a soft mask, a `/Mask`, a stencil flag or a non-default `/Decode`.

## Corpus

The corpus is not distributed (papers under publisher copyright, a private
notes export, a conference handbook). The table describes it well enough to
assemble an equivalent one.

| Name | Type | Size | Pages | Images | Notable | Budget |
| --- | --- | --- | --- | --- | --- | --- |
| Study Notes | iPad handwriting app export | 27.5 MB | 74 | 76 | every image tagged Display P3 ICC | 10 MiB |
| CycleGAN | arXiv 1703.10593 | 37.6 MB | 18 | 650 | 308 soft masks, JPX images, 330 links | 8 MiB |
| arXiv 2211 | arXiv 2211.17091v4 | 28.9 MB | 48 | 160 | 48 soft masks, 431 links, vector-field figures | 6 MiB |
| Nature | Nature article PDF | 10.4 MB | 12 | 29 | 4250 px photographs, small chart PNGs | 3 MiB |
| Handbook | conference programme (InDesign) | 26.6 MB | 42 | 181 | 162 DeviceCMYK JPEGs, text outlined to vectors (13.3 MB) | 8 MiB |
| Textbook | scanned statistics textbook | 18.4 MB | 251 | 251 | 16-colour indexed page scans | 8 MiB |
| Slides | thesis-defence deck | 5.6 MB | 41 | 165 | 108 soft masks, 1.4 MB of fonts/vectors | 2 MiB |
| CVPR | CVPR 2024 paper (NeRF) | 10.0 MB | 11 | 12 | large PNG renders with soft masks | 3 MiB |

## Results

| Document | PaperSqueeze | PixShift |
| --- | --- | --- |
| Study Notes | 10,482,389 B (100.0%) · SSIM 0.9910 / 0.9699 · **5 s** | 10,283,650 B (98.1%) · SSIM 0.9915 / 0.9724 · 52 s |
| CycleGAN | 8,310,231 B (99.1%) · SSIM **0.9985** / 0.9928 · **8 s** | 8,379,020 B (99.9%) · SSIM 0.9941 / 0.9819 · 270 s |
| arXiv 2211 | 6,273,262 B (99.7%) · SSIM 0.9775 / 0.8920 · **15 s** | **failed** (`target_size_unreachable`) · 208 s |
| Nature | 3,130,022 B (99.5%) · SSIM 0.9998 / 0.9987 · **3 s** | 3,144,949 B (100.0%) · SSIM 0.9998 / 0.9987 · 33 s |
| Handbook | 13,529,697 B (161%, over) · SSIM 0.9979 / 0.9804 · 39 s | **failed** · 1468 s |
| Textbook | 8,325,624 B (99.2%) · SSIM 0.9813 / 0.8667 · **34 s** | **failed** · 1144 s |
| Slides | 2,859,992 B (136%, over) · SSIM 0.9821 / 0.9110 · 5 s | **failed** · 101 s |
| CVPR | 3,094,726 B (98.4%) · SSIM 0.9997 / 0.9985 · **3 s** | **failed** · 82 s |

SSIM is reported as mean / worst page. Percentages are output size relative to
the budget.

**Budget met: 6/8 vs 3/8. Total wall-clock: 111 s vs 3,356 s (30×).**
Text, link and soft-mask fingerprints of every PaperSqueeze output are
identical to the source; page counts and geometry are unchanged.

### Reading the results honestly

- **Where both tools met the budget** (three documents) quality is on par:
  Nature is identical (0.9998), CycleGAN favours PaperSqueeze by 0.0044 because
  it can re-encode the 308 soft-masked images PixShift has to skip, and Study
  Notes favours PixShift by 0.0005. That last gap is not sharpness: PixShift
  converts the Display P3 pixels to sRGB (smaller chroma, smaller file at the
  same quality) while PaperSqueeze keeps the wide-gamut colourspace; SSIM on a
  rendered page cannot reward the retained gamut.
- **PixShift fails on five of eight documents.** Without downscaling and
  without touching masked images it cannot reach the budget and produces no
  file at all, after spending up to 24 minutes trying.
- **Two budgets are unreachable for anyone who keeps the vector layer.** The
  handbook has 13.3 MB of outlined text and the slide deck 1.4 MB of fonts and
  vectors, more than the budget itself. PaperSqueeze returns the highest-quality
  rung within 10% of the smallest achievable size and the CLI flags it as
  `OK (over target)`; only the explicit `raster` mode could go further.
- **Worst pages below 0.9** are a 4.6× compression of a figure-heavy arXiv
  paper (worst page: a grid of pure-noise diffusion samples, whose texture
  cannot survive any resampling) and a 2.2× compression of 251 scanned pages
  (≈33 KB per page). They were inspected visually and are inherent to the
  budget, not encoding defects.
- **Metric caveats.** SSIM at 96 DPI is insensitive to detail beyond screen
  resolution and very sensitive to sub-pixel resampling shifts; a 6%
  downscale of a 600 DPI photograph costs ~0.01 SSIM while being invisible at
  300% zoom. It is used here because it is reproducible, not because it is
  perceptually complete.

## Rendering compatibility

Outputs were rendered page by page with three independent engines and
compared to the source:

| Engine | Result |
| --- | --- |
| MuPDF 1.28 (PyMuPDF) | identical page coverage, mean colour difference ≤ 1.7/255 |
| Ghostscript 10.07 | identical page coverage |
| macOS Quartz (Preview, CoreGraphics) | identical page coverage |

This matters because an earlier implementation built on `page.replace_image`
produced files that MuPDF rendered but Ghostscript and Preview showed with
blank pages or washed-out colours (literal `null` dictionary entries and a lost
ICC colourspace). PixShift 2.0 uses the same PyMuPDF call and inherits the
Ghostscript blank-page behaviour.

## Reproducing

```bash
# 1. describe your corpus
cat > corpus.json <<'JSON'
[
  {"name": "paper",  "path": "/data/paper.pdf",  "budget": "6MiB"},
  {"name": "scan",   "path": "/data/scan.pdf",   "budget": "8MiB"}
]
JSON

# 2. run PaperSqueeze alone ...
uv run scripts/benchmark_corpus.py --manifest corpus.json --out bench/

# 3. ... or head-to-head with PixShift (installed on demand through uvx)
uv run scripts/benchmark_corpus.py --manifest corpus.json --out bench/ --pixshift
```

`bench/results.json` holds every measurement; the Markdown table is printed at
the end. Rerunning re-compresses with PaperSqueeze and re-scores PixShift's
existing outputs without rerunning it. Keep source files on a plain local
path: files inside another application's sandboxed container can block
`open()` on macOS privacy prompts when the benchmark runs from a terminal
multiplexer.

`scripts/compare_pdf_quality.py` clears MuPDF's decoded-resource store after
every page. Without that, rendering the original and the compressed file
alternately in one process can hand one document's cached image to the other
(their object numbers overlap after garbage collection) and report SSIM values
as low as 0.31 for pages that are in fact identical.
