from __future__ import annotations

import shutil
import threading
import time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

from PIL import Image, ImageFilter, ImageEnhance
import statistics

from .models import CompressionConfig
from .utils import clamp_quality


def _analyze_page_content(image: Image.Image) -> dict[str, float]:
    """Analyze page content to determine optimal compression strategy.

    Uses pure PIL operations (no numpy dependency).
    Returns dict with:
    - text_ratio: 0.0-1.0, higher means more text content
    - edge_density: 0.0-1.0, higher means more edges (text, line art)
    - color_variance: 0.0-1.0, higher means more color variation (photos)
    """
    # Downsample for fast analysis
    w, h = image.size
    scale = max(1, min(w, h) // 200)
    small = image.resize((w // scale, h // scale), Image.NEAREST)

    gray = small.convert("L") if small.mode != "L" else small

    # Edge detection via PIL FIND_EDGES
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_pixels = 0
    total_pixels = 0
    for pixel in edges.tobytes():
        total_pixels += 1
        if pixel > 30:
            edge_pixels += 1
    edge_density = min(1.0, edge_pixels / max(1, total_pixels) * 3)

    # Color variance
    if small.mode == "RGB":
        r, g, b = small.split()
        r_var = statistics.pvariance(r.tobytes())
        g_var = statistics.pvariance(g.tobytes())
        b_var = statistics.pvariance(b.tobytes())
        color_var = (r_var + g_var + b_var) / 3.0 / (128.0 * 128.0)
    else:
        color_var = statistics.pvariance(gray.tobytes()) / (128.0 * 128.0)
    color_variance = min(1.0, color_var)

    # Text ratio: high edge density + low color variance = text
    text_ratio = max(0.0, min(1.0, edge_density * 2 - color_variance))

    return {
        "text_ratio": text_ratio,
        "edge_density": edge_density,
        "color_variance": color_variance,
    }


def _content_adjusted_params(dpi: int, quality: int, content: dict[str, float]) -> tuple[int, int]:
    """Adjust DPI and quality based on page content analysis.

    Strategy:
    - Text-heavy: boost DPI (keep text sharp), slightly lower JPEG quality
    - Image-heavy: lower DPI, boost JPEG quality (preserve color/gradient)
    - Mixed: keep balanced
    """
    text_ratio = content["text_ratio"]

    if text_ratio > 0.6:
        # Text-heavy: prioritize sharpness
        dpi_boost = int(dpi * 0.15)  # +15% DPI
        quality_adj = -3  # slightly lower quality OK for text
    elif text_ratio < 0.3:
        # Image-heavy: prioritize color preservation
        dpi_boost = -int(dpi * 0.1)  # -10% DPI
        quality_adj = 5  # higher quality for images
    else:
        # Mixed: slight boost to both
        dpi_boost = int(dpi * 0.05)  # +5% DPI
        quality_adj = 2

    return max(_MIN_DPI, dpi + dpi_boost), clamp_quality(quality + quality_adj)


def _post_process_page(image: Image.Image, quality: int, content: Optional[dict[str, float]] = None) -> Image.Image:
    """Apply intelligent post-processing to improve readability after lossy compression.

    Content-aware adjustments:
    - Text-heavy pages: stronger sharpening, contrast boost
    - Image-heavy pages: lighter sharpening, preserve color
    - Mixed pages: balanced approach
    """
    if quality >= 75:
        return image

    # Calculate processing intensity (0.0 = mild, 1.0 = strong)
    intensity = max(0.0, min(1.0, (75 - quality) / 40.0))

    # Content-aware intensity adjustment
    text_ratio = content.get("text_ratio", 0.5) if content else 0.5
    if text_ratio > 0.6:
        # Text-heavy: boost sharpening intensity
        intensity = min(1.0, intensity * 1.3)
    elif text_ratio < 0.3:
        # Image-heavy: reduce sharpening to avoid artifacts
        intensity *= 0.7

    # Unsharp mask for text sharpening
    radius = 1 + intensity * 1.5  # 1.0 to 2.5
    percent = int(80 + intensity * 120)  # 80% to 200%
    threshold = 2
    image = image.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))

    # Contrast enhancement for text clarity
    contrast_factor = 1.0 + intensity * 0.15  # 1.0 to 1.15
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(contrast_factor)

    # For very low quality, also apply edge enhancement
    if quality < 45:
        edge_intensity = (45 - quality) / 45.0  # 0 to 1
        # Blend original with edge-enhanced version
        edges = image.filter(ImageFilter.EDGE_ENHANCE)
        blend_factor = edge_intensity * 0.3  # subtle blend
        image = Image.blend(image, edges, blend_factor)

    return image


def compress_pdf(source: Path, output: Path, config: CompressionConfig) -> Path:
    if config.pdf_mode not in {"auto", "optimize", "raster"}:
        raise ValueError("pdf_mode must be auto, optimize, or raster")
    if config.pdf_mode == "optimize":
        return optimize_pdf(source, output, config)
    if config.pdf_mode == "raster":
        return rasterize_pdf_to_target(source, output, config)

    with TemporaryDirectory(prefix="pdf_compress_") as temp_dir:
        original_size = source.stat().st_size
        # Skip optimize pass if target is very aggressive (< 30% of original)
        skip_optimize = config.target_bytes is not None and original_size > 0 and config.target_bytes < original_size * 0.3
        if skip_optimize:
            optimized = None
            opt_size = original_size
        else:
            optimized = Path(temp_dir) / "optimized.pdf"
            optimize_pdf(source, optimized, config)
            opt_size = optimized.stat().st_size
        if config.target_bytes is None:
            if optimized is not None and opt_size < original_size:
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(optimized, output)
                return output
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
            return output
        if opt_size <= config.target_bytes:
            if optimized is None:
                return rasterize_pdf_to_target(source, output, config)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(optimized, output)
            return output
        rasterized = Path(temp_dir) / "rasterized.pdf"
        try:
            rasterize_pdf_to_target(source, rasterized, config)
        except Exception:
            if optimized is None:
                raise
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(optimized, output)
            return output
        raster_size = rasterized.stat().st_size
        if optimized is not None:
            best = rasterized if raster_size < opt_size else optimized
        else:
            best = rasterized if raster_size < original_size else None
        output.parent.mkdir(parents=True, exist_ok=True)
        if best is not None:
            shutil.copy2(best, output)
        else:
            shutil.copy2(source, output)
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
    under_target_path: Optional[Path] = None
    under_target_size: Optional[int] = None
    under_target_quality: Optional[int] = None
    over_target_path: Optional[Path] = None
    over_target_size: Optional[int] = None
    over_target_diff: Optional[int] = None
    over_target_quality: Optional[int] = None
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
                elif quality == best_quality and best_size is not None and size < best_size:
                    best_path = candidate
                    best_size = size
            if size <= config.target_bytes:
                if under_target_size is None or size > under_target_size:
                    under_target_path = candidate
                    under_target_size = size
                    under_target_quality = quality
            else:
                diff = size - config.target_bytes
                if over_target_diff is None or diff < over_target_diff:
                    over_target_path = candidate
                    over_target_size = size
                    over_target_diff = diff
                    over_target_quality = quality
        if best_path is not None and best_size is not None and config.target_bytes is not None and best_size > config.target_bytes and under_target_path is not None and best_quality is not None and best_quality <= (under_target_quality or 0) + 10:
            best_path = under_target_path
            best_size = under_target_size
        if best_path is None:
            if under_target_path is not None:
                best_path = under_target_path
                best_size = under_target_size
            else:
                best_path = over_target_path
                best_size = over_target_size
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
            pix = None  # release native pixmap buffer immediately

            # Content-aware optimization
            content = _analyze_page_content(image)
            page_dpi, page_quality = _content_adjusted_params(dpi, quality, content)

            # Re-render at adjusted DPI if significantly different
            if abs(page_dpi - dpi) > 10:
                image.close()
                pix = page.get_pixmap(dpi=page_dpi, colorspace=colorspace, alpha=False, annots=True)
                image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
                pix = None

            image = _post_process_page(image, page_quality, content)
            data = BytesIO()
            try:
                image.save(data, format="JPEG", quality=clamp_quality(page_quality), optimize=True, progressive=True)
                rect = page.rect
                new_page = dst.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(new_page.rect, stream=data.getvalue())
            finally:
                image.close()
                data.close()
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
        return [(max(_MIN_DPI, config.pdf_dpi), clamp_quality(config.quality))]
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
        item = (max(_MIN_DPI, int(dpi)), max(_MIN_QUALITY, clamp_quality(int(quality))))
        if item not in seen:
            seen.add(item)
            values.append(item)
    return values


_render_cache: dict[tuple, object] = {}
_render_cache_lock = threading.Lock()
_RENDER_CACHE_MAX = 32


def _evict_oldest_result(allow_events: bool = False) -> bool:
    """Evict the oldest cache entry. Caller must hold _render_cache_lock.
    By default only evicts completed (non-Event) entries.
    If allow_events=True, evicts the oldest entry regardless of type."""
    for k in list(_render_cache):
        if allow_events or not isinstance(_render_cache[k], threading.Event):
            del _render_cache[k]
            return True
    return False


def _try_make_cache_room() -> bool:
    """Try to evict entries to make room in the cache. Caller must hold _render_cache_lock.
    Returns True if there is room (or room was made), False if cache is full of Events."""
    for _ in range(20):
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


def render_page(source: Path, page_index: int, dpi: int = 100) -> bytes:
    """Render a single PDF page as PNG bytes."""
    key = (str(source), page_index, dpi)
    wait_event: Optional[threading.Event] = None
    my_event: Optional[threading.Event] = None
    with _render_cache_lock:
        cached = _render_cache.get(key)
        if isinstance(cached, threading.Event):
            wait_event = cached
        elif cached is not None:
            return cached  # type: ignore[return-value]
        else:
            _try_make_cache_room()
            cached = _render_cache.get(key)
            if isinstance(cached, threading.Event):
                wait_event = cached
            elif cached is not None:
                return cached  # type: ignore[return-value]
            else:
                if len(_render_cache) >= _RENDER_CACHE_MAX:
                    _evict_oldest_result(allow_events=True)
                my_event = threading.Event()
                _render_cache[key] = my_event
    if wait_event is not None:
        if not wait_event.wait(timeout=30):
            result = getattr(wait_event, '_render_result', None)
            exc = getattr(wait_event, '_render_exc', None)
            with _render_cache_lock:
                _render_cache.pop(key, None)
            if result is not None:
                return result  # type: ignore[return-value]
            if exc is not None:
                raise exc  # type: ignore[misc]
            raise RuntimeError(f"Render timed out for {source} page {page_index}")
        if getattr(wait_event, '_render_cancelled', False):
            if not source.exists():
                raise FileNotFoundError(f"Source file was removed: {source}")
            # Cache entry was evicted but file still exists — fall through to render
        elif hasattr(wait_event, '_render_exc'):
            raise wait_event._render_exc  # type: ignore[attr-defined]
        else:
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
            existing = _render_cache.get(key)
            if existing is not None and not isinstance(existing, threading.Event):
                return existing  # type: ignore[return-value]
            event = _render_cache.pop(key, None)
            # Always signal the event, even if it was already popped from cache
            # (e.g., by a waiter that timed out during a lock-released window)
            signal_event = event or my_event
            if signal_event is not None:
                signal_event._render_result = result  # type: ignore[attr-defined]
                signal_event.set()
            if len(_render_cache) < _RENDER_CACHE_MAX:
                _render_cache[key] = result
        return result  # type: ignore[return-value]
    except Exception as exc:
        with _render_cache_lock:
            event = _render_cache.pop(key, None)
            signal_event = event or my_event
            if signal_event is not None:
                signal_event._render_exc = exc  # type: ignore[attr-defined]
                signal_event.set()
        raise


def evict_render_cache(source: Path) -> None:
    """Remove all cached renders for a specific source file."""
    prefix = str(source)
    with _render_cache_lock:
        for key in [k for k in _render_cache if k[0] == prefix]:
            entry = _render_cache[key]
            if isinstance(entry, threading.Event):
                entry._render_cancelled = True  # type: ignore[attr-defined]
                entry.set()
            del _render_cache[key]


def _fitz():
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF compression requires PyMuPDF. Install with: uv pip install -e .") from exc
    return fitz
