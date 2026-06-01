# PDF Manager

A local PDF compression and management tool with a web UI library. Upload PDFs, compress them to target sizes, and manage multiple compression versions — all running locally on your machine.

## Features

- **PDF Library** — persistent storage of all your PDFs with metadata, notes, and search
- **Smart Compression** — set a target size (e.g. 500KB) and the tool finds the best quality within that limit
- **Multiple Versions** — keep different compression variants of the same PDF (email version, archive version, etc.)
- **Three PDF Modes**:
  - `auto` — tries lossless optimization first, falls back to rasterization if needed
  - `optimize` — metadata cleanup + object rewriting, preserves selectable text
  - `raster` — re-renders pages as images, highest compression but loses text layer
- **CLI + Web UI** — use from command line or browse `http://localhost:8765`
- **Local-first** — everything stays on your machine, no cloud uploads

## Install

```bash
uv sync --extra dev
```

## Quick Start

Launch the web UI:

```bash
uv run file-compressor web
```

Open `http://127.0.0.1:8765` in your browser. Upload PDFs, set compression targets, download originals and compressed versions.

### CLI Usage

```bash
# Compress a single PDF
uv run file-compressor compress input.pdf

# Target 500KB with grayscale
uv run file-compressor compress input.pdf --target-size 500KB --pdf-grayscale

# Compress a whole directory
uv run file-compressor compress /path/to/pdfs --output-dir compressed

# JSON report
uv run file-compressor compress input.pdf --json-report
```

### Custom data directory

By default, the library stores data in `~/.pdf-manager`. Change it with:

```bash
uv run file-compressor web --data-dir /path/to/my/library
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/pdfs` | List all PDFs |
| POST | `/api/pdfs/upload` | Upload and optionally compress |
| GET | `/api/pdfs/:id/download` | Download original PDF |
| GET | `/api/pdfs/:id/versions` | List compression versions |
| POST | `/api/pdfs/:id/compress` | Create new compressed version |
| PUT | `/api/pdfs/:id/notes` | Update notes |
| DELETE | `/api/pdfs/:id` | Delete PDF and all versions |
| GET | `/api/versions/:id/download` | Download compressed version |
| DELETE | `/api/versions/:id` | Delete a version |
| GET | `/api/stats` | Library statistics |
| POST | `/compress` | Legacy single-file compress endpoint |

## Target Size

`--target-size` accepts formats like:
- `500KB`, `2MB`, `1.5GB`
- Plain bytes: `1000000`

The tool iterates through quality/DPI combinations to find the best result within the target. For PDFs, it tries lossless optimization first, then progressively lowers DPI and quality.
