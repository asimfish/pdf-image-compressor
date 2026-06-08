from __future__ import annotations

import shutil
import threading
import time
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

    with TemporaryDirectory(prefix="pdf_compress_") as temp_dir:
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
        rasterized = Path(temp_dir) / "rasterized.pdf"
        try:
            rasterize_pdf_to_target(source, rasterized, config)
        except RuntimeError:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(optimized, output)
            return output
        best = rasterized if rasterized.stat().st_size < opt_size else optimized
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, output)
        return output


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
    # Number of entries before quality starts decreasing monotonically
    _high_dpi_count = len(_HIGH_DPI_OFFSETS) + len(_MID_DPI_ENTRIES) + 1 + len(_MID_QUALITY_ENTRIES)

    with TemporaryDirectory(prefix="pdf_raster_") as temp_dir:
        temp = Path(temp_dir)
        for index, (dpi, quality) in enumerate(candidates):
            # Early exit: past the high-DPI zone, if current quality is below
            # the best we've found, no better candidate can appear.
            if config.target_bytes is not None and index > _high_dpi_count and best_quality is not None and quality < best_quality:
                break
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

# Mid-range entries: (dpi_cap, quality_cap) — dpi and quality are clamped to these caps
_MID_QUALITY_ENTRIES: list[tuple[int, int]] = [
    (140, 80), (140, 78), (135, 75), (130, 72),
    (125, 70), (120, 68), (120, 66),
]


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
    for dpi_cap, quality_cap in _MID_QUALITY_ENTRIES:
        base.append((min(start_dpi, dpi_cap), min(start_quality, quality_cap)))
    base.extend(_TAIL_CANDIDATES)
    seen: set[tuple[int, int]] = set()
    values: list[tuple[int, int]] = []
    for dpi, quality in base:
        item = (max(_MIN_DPI, int(dpi)), clamp_quality(int(quality)))
        if item not in seen:
            seen.add(item)
            values.append(item)
    return values


_render_cache: dict[tuple, object] = {}
_render_cache_lock = threading.Lock()
_RENDER_CACHE_MAX = 32


def _evict_oldest_result() -> bool:
    """Evict the oldest completed (non-Event) cache entry. Caller must hold _render_cache_lock.
    Returns True if an entry was evicted, False if all entries are in-flight Events."""
    for k in list(_render_cache):
        if not isinstance(_render_cache[k], threading.Event):
            del _render_cache[k]
            return True
    return False


def _try_make_cache_room() -> bool:
    """Try to evict entries to make room in the cache. Caller must hold _render_cache_lock.
    Returns True if there is room (or room was made), False if cache is full of Events."""
    for _ in range(5):
        if len(_render_cache) < _RENDER_CACHE_MAX:
            return True
        if _evict_oldest_result():
            return True
        _render_cache_lock.release()
        try:
            time.sleep(0.05)
        finally:
            _render_cache_lock.acquire()
    return len(_render_cache) < _RENDER_CACHE_MAX


def render_page(source: Path, page_index: int, dpi: int = 150) -> bytes:
    """Render a single PDF page as PNG bytes."""
    key = (str(source), page_index, dpi)
    wait_event: Optional[threading.Event] = None
    with _render_cache_lock:
        cached = _render_cache.get(key)
        if isinstance(cached, threading.Event):
            wait_event = cached
        elif cached is not None:
            return cached  # type: ignore[return-value]
        else:
            _try_make_cache_room()
            _render_cache[key] = threading.Event()
    if wait_event is not None:
        wait_event.wait()
        if hasattr(wait_event, '_render_exc'):
            raise wait_event._render_exc  # type: ignore[attr-defined]
        result = getattr(wait_event, '_render_result', None)
        if result is None:
            raise RuntimeError(f"Render failed for {source} page {page_index}")
        return result  # type: ignore[return-value]
    try:
        fitz = _fitz()
        doc = fitz.open(source)
        try:
            if page_index < 0 or page_index >= len(doc):
                raise ValueError(f"Page index {page_index} out of range (0-{len(doc) - 1})")
            page = doc[page_index]
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            result: object = pix.tobytes("png")
        finally:
            doc.close()
        with _render_cache_lock:
            _try_make_cache_room()
            event = _render_cache.pop(key, None)
            if event is None:
                return result  # type: ignore[return-value]
            _render_cache[key] = result
            event._render_result = result  # type: ignore[attr-defined]
            event.set()
        return result  # type: ignore[return-value]
    except Exception as exc:
        with _render_cache_lock:
            event = _render_cache.pop(key, None)
            if event is not None:
                event._render_exc = exc  # type: ignore[attr-defined]
                event.set()
        raise


def evict_render_cache(source: Path) -> None:
    """Remove all cached renders for a specific source file."""
    prefix = str(source)
    with _render_cache_lock:
        for key in [k for k in _render_cache if k[0] == prefix]:
            del _render_cache[key]


def _fitz():
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF compression requires PyMuPDF. Install with: uv pip install -e .") from exc
    return fitz
