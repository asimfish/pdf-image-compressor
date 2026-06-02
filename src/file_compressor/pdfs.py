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
        if config.target_bytes is None or optimized.stat().st_size <= config.target_bytes:
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


def rasterize_pdf_to_target(source: Path, output: Path, config: CompressionConfig) -> Path:
    candidates = _pdf_candidates(config)
    best_path: Optional[Path] = None
    best_size: Optional[int] = None

    with TemporaryDirectory(prefix="pdf_raster_") as temp_dir:
        temp = Path(temp_dir)
        for index, (dpi, quality) in enumerate(candidates):
            candidate = temp / f"candidate_{index}_{dpi}_{quality}.pdf"
            rasterize_pdf(source, candidate, dpi=dpi, quality=quality, grayscale=config.pdf_grayscale, strip_metadata=config.strip_metadata)
            size = candidate.stat().st_size
            if best_size is None or size < best_size:
                best_path = candidate
                best_size = size
            if config.target_bytes is None or size <= config.target_bytes:
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, output)
                return output
        if best_path is None:
            raise RuntimeError("PDF compression produced no output")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best_path, output)
        return output


def rasterize_pdf(source: Path, output: Path, dpi: int, quality: int, grayscale: bool, strip_metadata: bool) -> Path:
    fitz = _fitz()
    src = fitz.open(source)
    dst = fitz.open()
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


def _pdf_candidates(config: CompressionConfig) -> list[tuple[int, int]]:
    if config.target_bytes is None:
        return [(config.pdf_dpi, config.quality)]
    start_dpi = max(36, config.pdf_dpi)
    start_quality = max(1, min(95, config.quality))
    base = [
        (start_dpi, start_quality),
        (min(start_dpi, 140), min(start_quality, 78)),
        (min(start_dpi, 130), min(start_quality, 72)),
        (min(start_dpi, 120), min(start_quality, 66)),
        (110, 58),
        (105, 50),
        (100, 44),
        (95, 40),
        (90, 36),
        (85, 32),
        (80, 30),
        (72, 28),
        (65, 25),
        (60, 22),
        (55, 20),
        (50, 18),
        (45, 16),
    ]
    seen: set[tuple[int, int]] = set()
    values: list[tuple[int, int]] = []
    for dpi, quality in base:
        item = (max(36, int(dpi)), max(1, min(95, int(quality))))
        if item not in seen:
            seen.add(item)
            values.append(item)
    return values


def _fitz():
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF compression requires PyMuPDF. Install with: uv pip install -e .") from exc
    return fitz
