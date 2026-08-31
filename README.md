<div align="center">

# PaperSqueeze 📄🗜️

[![CI](https://github.com/asimfish/pdf-image-compressor/actions/workflows/ci.yml/badge.svg)](https://github.com/asimfish/pdf-image-compressor/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/asimfish/pdf-image-compressor)](https://github.com/asimfish/pdf-image-compressor/releases)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Live Demo](https://img.shields.io/badge/live-demo-126b4f.svg)](https://liyufeng854--papersqueeze-serve.modal.run)

**English** | [中文](README_CN.md)

> 🎯 **Squeeze a paper PDF down to any target size — without killing the text.**
> PaperSqueeze re-encodes only the images that actually take up space, and keeps
> searchable text, formulas, hyperlinks, vector graphics, and transparency intact.

[**Try the live demo →**](https://liyufeng854--papersqueeze-serve.modal.run) *(free instance, may need a cold start)*

</div>

---

## Contents

1. [Why PaperSqueeze](#1-why-papersqueeze)
2. [Quick Start](#2--quick-start)
3. [Features](#3--features)
4. [Real-World Benchmark](#4--real-world-benchmark)
5. [Compression Modes](#5--compression-modes)
6. [CLI Usage](#6--cli-usage)
7. [Web UI](#7--web-ui)
8. [Deployment](#8--deployment)
9. [Configuration](#9--configuration)
10. [API](#10--api)
11. [Testing & Development](#11--testing--development)
12. [Contributing](#12--contributing)
13. [Citation](#13--citation)
14. [Star History](#14--star-history)
15. [License](#15-license)

---

## 1. Why PaperSqueeze

Most PDF compressors hit a size target by rasterizing whole pages — your paper
becomes a stack of screenshots: no text selection, no search, dead links, blurry
formulas. Conference submission portals don't care; your readers do.

PaperSqueeze takes the opposite approach:

- **Only images are re-encoded.** Text, fonts, vector figures, and link
  annotations pass through untouched.
- **Transparency survives.** External soft masks (`SMask`) are restored after
  re-encoding, so translucent highlights don't turn into black or white boxes.
- **Rasterization is never silent.** The default `fidelity` mode and the
  `auto` mode refuse to rasterize; if the target is unreachable while keeping
  the text layer, you get the closest faithful result — only an explicit
  `raster` mode trades away the text layer.

## 2. 🚀 Quick Start

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install
git clone https://github.com/asimfish/pdf-image-compressor.git
cd pdf-image-compressor
uv sync --locked --extra dev

# 2. Compress to a target size (default fidelity mode — never rasterizes)
uv run file-compressor compress paper.pdf --target-size 3MB

# 3. Or use the browser UI
uv run file-compressor web        # → http://127.0.0.1:8765
```

No install at all? Use the [live demo](https://liyufeng854--papersqueeze-serve.modal.run)
or run the published container:

```bash
docker run --rm -p 8080:8080 -e PDF_COMPRESSOR_PUBLIC_MODE=1 \
  ghcr.io/asimfish/pdf-image-compressor:latest   # → http://127.0.0.1:8080
```

## 3. ✨ Features

- 🎯 **Target-size compression** — give it `500KB`, `3MB`, or a byte count;
  it searches for the best quality that fits under the cap.
- 🔤 **Text-first** — searchable text, copy/paste, and hyperlinks survive in
  every mode except the explicit `raster` mode.
- 🪟 **Transparency-faithful** — restores external PDF soft masks (including
  partial alpha, shared masks, indirect subtypes, and `Matte`) after image
  re-encoding.
- 🧭 **Honest auto mode** — lossless optimization → text-preserving image
  recompression; if the target is unreachable it returns the closest faithful
  result instead of silently rasterizing.
- 🎚️ **Four intensity levels** — 1 near-lossless, 2 balanced, 3 aggressive,
  4 maximum.
- 🖥️ **CLI + Web UI** — single files, directory batches, JSON reports, or a
  browser workflow; standalone images (JPEG/PNG/WebP) are supported too.
- 🌐 **Two site modes** — a persistent local PDF library, or an anonymous
  stateless public site with automatic temp-file cleanup.
- 🔐 **Optional access gate** — protect a public deployment with a password
  (`PDF_COMPRESSOR_ACCESS_PASSWORD`); visitors authenticate once per browser
  for 30 days.
- 📦 **Verified container** — every `main` push is built, smoke-tested, and
  published to GitHub Container Registry; releases get versioned tags.

## 4. 📊 Real-World Benchmark

The deployed demo compressed a 20-page, 30,628,839-byte paper PDF with the
default 3 MB target, compared against a 3,194,322-byte reference file:

| Metric | Result |
| --- | --- |
| Output size | **2,773,250 bytes** (421,072 bytes under the reference) |
| Compression ratio | **90.95%** |
| Text | 80,827 characters — identical to the original |
| Links | 163 — identical to the original |
| Transparency | all **65 SMask references preserved** |
| Visual quality | RGB multichannel SSIM avg **0.9993**, min 0.9961 (20 pages) |
| End-to-end time | ~62 s online (upload + process + download); 61–84 s locally |

Reproduce it with the self-contained benchmark (deps are installed ephemerally
by `uv`, never entering the production image):

```bash
uv run scripts/compare_pdf_quality.py original.pdf compressed.pdf \
  --reference reference.pdf --dpi 96

# machine-readable report
uv run scripts/compare_pdf_quality.py original.pdf compressed.pdf --json
```

The comparison requires identical page counts and page geometry — no scaling or
cropping can mask structural changes. The test paper is not included in the
repository; evaluation uses "same original, same byte cap" rather than
misleading pixel diffs of different content.

## 5. 🧭 Compression Modes

| Mode | Text layer | What it does |
| --- | --- | --- |
| `fidelity` *(default)* | ✅ kept | Quality-first: lossless optimization, then gentle image recompression; prefers the lossless candidate when it fits the target |
| `auto` | ✅ kept | Stronger target search than `fidelity`; still never rasterizes — returns the closest faithful result if the target is unreachable |
| `text` | ✅ kept | Maximum text-preserving image recompression |
| `optimize` | ✅ kept | Lossless structural optimization only |
| `raster` | ❌ lost | Rasterizes pages at `--pdf-dpi` — the only mode that trades away text; never chosen implicitly |

## 6. ⌨️ CLI Usage

```bash
# Default: fidelity-first, no rasterization, aim under 3 MB
uv run file-compressor compress input.pdf --target-size 3MB

# Stronger target search, still text-safe
uv run file-compressor compress input.pdf --target-size 3MB --pdf-mode auto

# Keep text, allow harder image recompression
uv run file-compressor compress input.pdf --target-size 3MB --pdf-mode text

# Extreme compression — loses the text layer (explicit opt-in)
uv run file-compressor compress input.pdf --target-size 800KB \
  --pdf-mode raster --pdf-dpi 120

# Batch a directory with a JSON report
uv run file-compressor compress ./papers --output-dir ./compressed --json-report
```

`--target-size` accepts `500KB`, `2MB`, `1.5GB`, or a plain byte count.
Other useful flags: `--compression-level 1..4`, `--pdf-grayscale`,
`--keep-metadata`, `--to-webp` (images), `--archive zip`.

## 7. 🖥️ Web UI

**Local library mode** (persistent):

```bash
uv run file-compressor web                          # → http://127.0.0.1:8765
uv run file-compressor web --data-dir /path/to/lib  # custom library location
```

PDFs, notes, and compression versions are stored in `~/.pdf-manager`. This mode
has no authentication — bind it to localhost or a trusted network only.

**Public stateless mode** (anonymous): exposes only the landing page, config,
health check, and single-file compression; every upload is processed in an
isolated temp directory and deleted after the response. Restrictive security
headers are applied and OpenAPI docs are disabled.

```bash
PDF_COMPRESSOR_PUBLIC_MODE=1 uv run file-compressor web --host 0.0.0.0 --port 8080
```

See [Configuration](#9--configuration) for limits, timeouts, rate limiting, and
the optional password gate. A commented [`.env.example`](.env.example) is included.

## 8. ☁️ Deployment

```bash
docker run --rm -p 8080:8080 -e PDF_COMPRESSOR_PUBLIC_MODE=1 \
  ghcr.io/asimfish/pdf-image-compressor:latest
```

Step-by-step guides for **Docker / Modal (free tier) / Hugging Face Spaces /
Google Cloud Run** live in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
([中文](docs/DEPLOYMENT_CN.md)). The image is platform-neutral OCI — it runs
anywhere containers do.

## 9. ⚙️ Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `PDF_COMPRESSOR_PUBLIC_MODE` | off | Set `1` to enable the anonymous stateless public site |
| `PDF_COMPRESSOR_ACCESS_PASSWORD` | unset | Optional public-mode password gate; visitors authenticate once per browser for 30 days |
| `PDF_COMPRESSOR_MAX_UPLOAD_MB` | 30 public / 500 library | Per-file upload cap (1–500 MiB) |
| `PDF_COMPRESSOR_MAX_PAGES` | 100 | Public-mode page cap (1–2000) |
| `PDF_COMPRESSOR_UPLOAD_TIMEOUT_SECONDS` | 120 | Public-mode total upload window (10–900 s) |
| `PDF_COMPRESSOR_PROCESSING_TIMEOUT_SECONDS` | 300 | Public-mode compression cap (30–1800 s); the isolated worker is killed on timeout |
| `PDF_COMPRESSOR_DOWNLOAD_TIMEOUT_SECONDS` | 120 | Public-mode download window (10–900 s); temp files are cleaned on timeout |
| `PDF_COMPRESSOR_RATE_LIMIT_PER_MINUTE` | 12 | Compression requests accepted per instance per minute (1–120) |
| `PDF_COMPRESSOR_CONCURRENCY` | 1 | Concurrent compression jobs per instance (1–4) |
| `PORT` | 8080 | Container listen port |

## 10. 🔌 API

Public mode:

- `GET /` — compression page
- `GET /api/health` — health check
- `GET /api/config` — upload limits for the frontend
- `POST /compress` — upload one PDF, receive the compressed PDF

Local library mode additionally provides `/api/pdfs`, `/api/versions`, batch
compression/deletion, page previews, notes, and version downloads. In dev mode
the full OpenAPI schema is at `/docs`.

## 11. 🧪 Testing & Development

```bash
uv sync --locked --extra dev
uv run ruff check src tests scripts deploy_huggingface_space.py deploy_modal.py
uv run pip-audit --local --skip-editable
uv run python -m pytest -q
uv build
```

The suite currently has **414 tests** covering CLI, PDF/image compression,
target sizing, text & transparency preservation (partial alpha, shared soft
masks, indirect subtypes, `Matte`), visual-quality benchmarks, the web API,
deployment packaging, storage, and error handling. CI runs Ruff, dependency
audits, and the tests on Python 3.10 and 3.12, then builds the distribution and
verifies the container.

## 12. 🤝 Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Changes to compression behavior must report sizes, text/link/soft-mask
preservation, at least one visual metric (SSIM/PSNR), runtime & peak memory,
and include a regression test. Report vulnerabilities privately via
[SECURITY.md](SECURITY.md).

## 13. 📖 Citation

If PaperSqueeze helps your work, please cite it (see [CITATION.cff](CITATION.cff)):

```bibtex
@software{papersqueeze,
  author  = {Li, Yufeng},
  title   = {PaperSqueeze: target-aware PDF compression that preserves searchable text, links, and vector content},
  year    = {2026},
  url     = {https://github.com/asimfish/pdf-image-compressor}
}
```

## 14. ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=asimfish/pdf-image-compressor&type=Date)](https://star-history.com/#asimfish/pdf-image-compressor&Date)

## 15. License

[MIT](LICENSE)
