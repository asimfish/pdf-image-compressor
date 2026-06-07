from __future__ import annotations

import shutil
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

from PIL import Image

from .models import CompressionConfig
from .utils import clamp_quality


def compress_pdf(source: Path, output: Path, config: CompressionConfig) -> Path:
    if config.pdf_mode not in {"auto", "optimize", "raster"}:
        raise ValueError("pdf_mode must be auto, optimize, or raster")
    if config.pdf_mode == "optimize":
        return optimize_pdf(source, output, config)
    if config.pdf_mode == "raster":
        return rasterize_pdf_to_target(source, output, config)

    with TemporaryDirectory(prefix="pdf_optimize_") as temp_dir:
        optimized = Path(temp_dir) / "optimized.pdf"
        optimize_pdf(source, optimized, config)
        opt_size = optimized.stat().st_size
        if config.target_bytes is None:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(optimized, output)
            return output
        if opt_size <= config.target_bytes * 1.02:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(optimized, output)
            return output
    return rasterize_pdf_to_target(source, output, config)


def optimize_pdf(source: Path, output: Path, config: CompressionConfig) -> Path:
    fitz = _fitz()
    doc = fitz.open(source)
    try:
        if config.strip_metadata:
            doc.set_metadata({})
            if hasattr(doc, "del_xml_metadata"):
                doc.del_xml_metadata()
        output.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output, garbage=4, deflate=True, clean=True)
        return output
    finally:
        doc.close()


_SIZE_TOLERANCE = 1.02


def rasterize_pdf_to_target(source: Path, output: Path, config: CompressionConfig) -> Path:
    candidates = _pdf_candidates(config)
    best_path: Optional[Path] = None
    best_size: Optional[int] = None
    best_quality: Optional[int] = None
    closest_path: Optional[Path] = None
    closest_size: Optional[int] = None
    best_diff: Optional[int] = None

    with TemporaryDirectory(prefix="pdf_raster_") as temp_dir:
        temp = Path(temp_dir)
        for index, (dpi, quality) in enumerate(candidates):
            candidate = temp / f"candidate_{index}_{dpi}_{quality}.pdf"
            rasterize_pdf(source, candidate, dpi=dpi, quality=quality, grayscale=config.pdf_grayscale, strip_metadata=config.strip_metadata)
            size = candidate.stat().st_size
            if config.target_bytes is None:
                if best_size is None or size < best_size:
                    best_path = candidate
                    best_size = size
                continue
            if size <= config.target_bytes * _SIZE_TOLERANCE:
                if best_quality is None or quality > best_quality:
                    best_path = candidate
                    best_size = size
                    best_quality = quality
            diff = abs(size - config.target_bytes)
            if best_diff is None or diff < best_diff:
                closest_path = candidate
                closest_size = size
                best_diff = diff
        if best_path is None and closest_path is not None:
            best_path = closest_path
            best_size = closest_size
        if best_path is None:
            raise RuntimeError("PDF compression produced no output")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best_path, output)
        return output


def rasterize_pdf(source: Path, output: Path, dpi: int, quality: int, grayscale: bool, strip_metadata: bool) -> Path:
    fitz = _fitz()
    src = fitz.open(source)
    try:
        dst = fitz.open()
    except Exception:
        src.close()
        raise
    try:
        for page in src:
            colorspace = fitz.csGRAY if grayscale else fitz.csRGB
            pix = page.get_pixmap(dpi=dpi, colorspace=colorspace, alpha=False, annots=True)
            mode = "L" if grayscale else "RGB"
            image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
            data = BytesIO()
            image.save(data, format="JPEG", quality=clamp_quality(quality), optimize=True, progressive=True)
            rect = page.rect
            new_page = dst.new_page(width=rect.width, height=rect.height)
            new_page.insert_image(new_page.rect, stream=data.getvalue())
        if strip_metadata:
            dst.set_metadata({})
        output.parent.mkdir(parents=True, exist_ok=True)
        dst.save(output, garbage=4, deflate=True)
        return output
    finally:
        dst.close()
        src.close()


_MIN_DPI = 36
_MIN_QUALITY = 16

# Fixed-quality tail entries (dpi, quality) — appended after the dynamic head
_TAIL_CANDIDATES: list[tuple[int, int]] = [
    (110, 58), (105, 54), (105, 50), (100, 47), (100, 44),
    (95, 42), (95, 40), (90, 38), (90, 36), (85, 34),
    (85, 32), (80, 30), (75, 29), (72, 28), (68, 27),
    (65, 25), (60, 22), (55, 20), (50, 18), (45, _MIN_QUALITY),
]

# Quality offsets applied to start_quality for the high-DPI head entries
_HIGH_DPI_OFFSETS: list[tuple[int, int]] = [
    (300, 10), (280, 8), (260, 6), (240, 5), (220, 3),
]

# Fixed-DPI entries that use start_quality (no offset)
_MID_DPI_ENTRIES: list[int] = [200, 180, 160, 150]


def _pdf_candidates(config: CompressionConfig) -> list[tuple[int, int]]:
    if config.target_bytes is None:
        return [(config.pdf_dpi, config.quality)]
    start_dpi = max(_MIN_DPI, config.pdf_dpi)
    start_quality = clamp_quality(config.quality)
    base: list[tuple[int, int]] = []
    for dpi, offset in _HIGH_DPI_OFFSETS:
        base.append((dpi, min(start_quality + offset, 95)))
    for dpi in _MID_DPI_ENTRIES:
        base.append((dpi, start_quality))
    base.append((start_dpi, start_quality))
    base.append((min(start_dpi, 140), min(start_quality, 80)))
    base.append((min(start_dpi, 140), min(start_quality, 78)))
    base.append((min(start_dpi, 135), min(start_quality, 75)))
    base.append((min(start_dpi, 130), min(start_quality, 72)))
    base.append((min(start_dpi, 125), min(start_quality, 70)))
    base.append((min(start_dpi, 120), min(start_quality, 68)))
    base.append((min(start_dpi, 120), min(start_quality, 66)))
    base.extend(_TAIL_CANDIDATES)
    seen: set[tuple[int, int]] = set()
    values: list[tuple[int, int]] = []
    for dpi, quality in base:
        item = (max(_MIN_DPI, int(dpi)), clamp_quality(int(quality)))
        if item not in seen:
            seen.add(item)
            values.append(item)
    return values


def render_page(source: Path, page_index: int, dpi: int = 150) -> bytes:
    """Render a single PDF page as PNG bytes."""
    fitz = _fitz()
    doc = fitz.open(source)
    try:
        if page_index < 0 or page_index >= len(doc):
            raise ValueError(f"Page index {page_index} out of range (0-{len(doc) - 1})")
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def _fitz():
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF compression requires PyMuPDF. Install with: uv pip install -e .") from exc
    return fitz
