from __future__ import annotations

import io
import logging
import shutil
import threading
import time
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Protocol

from PIL import Image, ImageFilter, ImageEnhance, ImageStat

from .models import CompressionConfig
from .utils import clamp_quality

logger = logging.getLogger(__name__)


class _PdfSoftMaskInspector(Protocol):
    def is_stream(self, xref: int) -> bool: ...

    def xref_get_key(self, xref: int, key: str) -> tuple[str, str]: ...

    def xref_object(self, xref: int, *, compressed: bool) -> str: ...


class _PdfDictionaryDocument(Protocol):
    def xref_get_key(self, xref: int, key: str) -> tuple[str, str]: ...


class _PdfImageUpdater(Protocol):
    def xref_set_key(self, xref: int, key: str, value: str) -> None: ...


class _PdfImagePage(Protocol):
    def replace_image(self, xref: int, *, stream: bytes) -> None: ...


class _SoftMaskRestoreError(RuntimeError):
    """Raised after image replacement when its PDF soft mask cannot be restored."""


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

    # Edge detection via PIL FIND_EDGES. Count strong-edge pixels (>30) from the
    # histogram instead of a per-pixel Python loop — same result, C-level speed.
    edges = gray.filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    total_pixels = sum(hist)
    edge_pixels = sum(hist[31:])
    edge_density = min(1.0, edge_pixels / max(1, total_pixels) * 3)

    # Color variance via ImageStat (population variance, computed in C) rather than
    # statistics.pvariance over raw bytes, which is far slower for large pages.
    if small.mode == "RGB":
        var = ImageStat.Stat(small).var
        color_var = (var[0] + var[1] + var[2]) / 3.0 / (128.0 * 128.0)
    else:
        color_var = ImageStat.Stat(gray).var[0] / (128.0 * 128.0)
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


def _prepare_pdf_image_for_jpeg(
    image: Image.Image,
    stack: ExitStack,
    *,
    doc: _PdfSoftMaskInspector,
    smask_xref: int,
) -> Image.Image:
    """Return a JPEG-compatible base image without destroying PDF transparency.

    External PDF soft masks stay separate and are restored after replacement.
    Pillow-embedded alpha has no separate PDF object, so it is flattened to white.
    Any derived images are owned by ``stack``.
    """
    mask: Optional[Image.Image] = None
    if smask_xref > 0:
        try:
            is_stream = doc.is_stream(smask_xref)
            subtype_type, subtype_value = (
                doc.xref_get_key(smask_xref, "Subtype") if is_stream else ("null", "null")
            )
            if subtype_type == "xref":
                reference = subtype_value.split()
                if (
                    len(reference) == 3
                    and reference[0].isascii()
                    and reference[0].isdigit()
                    and reference[1].isascii()
                    and reference[1].isdigit()
                    and reference[2] == "R"
                ):
                    subtype_type = "name"
                    subtype_value = doc.xref_object(
                        int(reference[0]),
                        compressed=True,
                    ).strip()
                else:
                    logger.warning(
                        "Skipping PDF soft mask %d: malformed indirect Subtype reference %r",
                        smask_xref,
                        subtype_value,
                    )
        except Exception as exc:
            logger.warning(
                "Unable to inspect PDF soft mask %d; skipping image compression: %s",
                smask_xref,
                exc,
            )
            raise ValueError(f"Unable to inspect PDF soft mask {smask_xref}") from exc
        if not is_stream or (subtype_type, subtype_value) != ("name", "/Image"):
            logger.warning(
                "Skipping PDF soft mask %d: xref does not reference a PDF image stream",
                smask_xref,
            )
            raise ValueError(f"Invalid PDF soft mask {smask_xref}")
    elif "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info):
        rgba = stack.enter_context(image.convert("RGBA"))
        mask = stack.enter_context(rgba.getchannel("A"))

    if mask is not None:
        rgb = image if image.mode == "RGB" else stack.enter_context(image.convert("RGB"))
        background = stack.enter_context(Image.new("RGB", image.size, "white"))
        background.paste(rgb, mask=mask)
        return background
    if image.mode == "CMYK" or image.mode not in ("RGB", "L"):
        return stack.enter_context(image.convert("RGB"))
    return image


def _soft_mask_requires_matching_dimensions(
    doc: _PdfDictionaryDocument,
    smask_xref: int,
) -> bool:
    """Return whether a Matte entry requires matching mask and base dimensions.

    PDF soft masks may otherwise use independent dimensions and are scaled by the
    renderer. Inspection failures lock dimensions so compression fails closed.
    """
    if smask_xref <= 0:
        return False
    try:
        value_type, _ = doc.xref_get_key(smask_xref, "Matte")
    except Exception as exc:
        logger.warning(
            "Unable to inspect Matte for PDF soft mask %d; preserving image dimensions: %s",
            smask_xref,
            exc,
        )
        return True
    return value_type != "null"


def _replace_pdf_image_preserving_soft_mask(
    page: _PdfImagePage,
    doc: _PdfImageUpdater,
    *,
    xref: int,
    smask_xref: int,
    stream: bytes,
) -> None:
    page.replace_image(xref, stream=stream)
    if smask_xref > 0:
        try:
            doc.xref_set_key(xref, "SMask", f"{smask_xref} 0 R")
        except Exception as exc:
            logger.error(
                "PDF image replacement succeeded for xref=%d, but soft mask %d could not be restored",
                xref,
                smask_xref,
            )
            raise _SoftMaskRestoreError(
                f"Unable to restore PDF soft mask {smask_xref} for image xref {xref}"
            ) from exc


def optimize_images_in_pdf(source: Path, output: Path, config: CompressionConfig) -> Path:
    """Smart compression: keep text as vector, only compress embedded images.

    Compression levels:
    - 1 (minimal): ratio=0.8, quality=85 - best quality, minimal compression
    - 2 (balanced): ratio=0.5, quality=70 - good balance (default)
    - 3 (aggressive): ratio=0.3, quality=50 - smaller files
    - 4 (maximum): ratio=0.15, quality=30 - smallest files
    """
    fitz = _fitz()
    doc = fitz.open(source)
    try:
        original_size = source.stat().st_size
        target_bytes = config.target_bytes
        level = max(1, min(4, config.compression_level))

        # Level-based defaults
        level_defaults = {
            1: {"ratio": 0.8, "quality": 85},  # Minimal
            2: {"ratio": 0.5, "quality": 70},  # Balanced
            3: {"ratio": 0.3, "quality": 50},  # Aggressive
            4: {"ratio": 0.15, "quality": 30},  # Maximum
        }
        defaults = level_defaults[level]

        # Calculate compression ratio needed for images
        if target_bytes is not None:
            # Estimate text overhead (usually 10-20% of file)
            text_overhead = original_size * 0.15
            image_budget = max(target_bytes - text_overhead, target_bytes * 0.3)
            # Image compression ratio
            total_image_size = 0
            image_xrefs: set[int] = set()
            for page in doc:
                for img in page.get_images(full=True):
                    xref = img[0]
                    if xref not in image_xrefs:
                        image_xrefs.add(xref)
                        base = doc.extract_image(xref)
                        if base:
                            total_image_size += len(base['image'])
            if total_image_size > 0:
                ratio = min(1.0, image_budget / total_image_size)
            else:
                ratio = defaults["ratio"]
        else:
            ratio = defaults["ratio"]

        # Process each image
        processed = set()
        for page in doc:
            images = page.get_images(full=True)
            for img in images:
                xref = img[0]
                smask_xref = img[1]
                if xref in processed:
                    continue
                processed.add(xref)

                base_image = doc.extract_image(xref)
                if not base_image:
                    continue

                img_bytes = base_image["image"]
                w, h = base_image["width"], base_image["height"]

                # Skip tiny images
                if w < 50 or h < 50 or len(img_bytes) < 1024:
                    continue

                try:
                    with ExitStack() as stack:
                        image_source = stack.enter_context(io.BytesIO(img_bytes))
                        pil_img = stack.enter_context(Image.open(image_source))
                        pil_img = _prepare_pdf_image_for_jpeg(
                            pil_img,
                            stack,
                            doc=doc,
                            smask_xref=smask_xref,
                        )

                        # Calculate new dimensions
                        new_w = max(64, int(w * ratio))
                        new_h = max(64, int(h * ratio))
                        dimensions_locked = _soft_mask_requires_matching_dimensions(
                            doc,
                            smask_xref,
                        )

                        # Only resize if it saves significant space
                        if not dimensions_locked and (
                            new_w < w * 0.9 or new_h < h * 0.9
                        ):
                            pil_img = stack.enter_context(
                                pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                            )

                        # Save as JPEG with quality based on level and ratio
                        base_quality = defaults["quality"]
                        quality = max(20, min(95, int(base_quality * ratio * 1.5)))
                        with io.BytesIO() as buf:
                            pil_img.save(buf, format="JPEG", quality=quality, optimize=True)
                            new_img_bytes = buf.getvalue()

                    if len(new_img_bytes) < len(img_bytes) and page.get_image_rects(xref):
                        _replace_pdf_image_preserving_soft_mask(
                            page,
                            doc,
                            xref=xref,
                            smask_xref=smask_xref,
                            stream=new_img_bytes,
                        )
                except _SoftMaskRestoreError:
                    raise
                except Exception as exc:
                    logger.debug("Skipping PDF image xref=%d during optimization: %s", xref, exc)
                    continue  # Skip problematic images

        # Save with garbage collection
        if config.strip_metadata:
            doc.set_metadata({})
        output.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output, garbage=4, deflate=True, clean=True)
        return output
    finally:
        doc.close()


def compress_pdf(source: Path, output: Path, config: CompressionConfig) -> Path:
    if config.pdf_mode not in {"auto", "fidelity", "optimize", "raster", "text"}:
        raise ValueError("pdf_mode must be auto, fidelity, optimize, raster, or text")
    if config.pdf_mode == "fidelity":
        return compress_pdf_fidelity(source, output, config)
    if config.pdf_mode == "optimize":
        return optimize_pdf(source, output, config)
    if config.pdf_mode == "raster":
        return rasterize_pdf_to_target(source, output, config)
    if config.pdf_mode == "text":
        return compress_pdf_keep_text(source, output, config)

    # Auto mode is quality-first. Preserve the original text/vector layer whenever
    # the requested target is achievable by recompressing embedded images. Full-page
    # rasterization is an expensive, destructive fallback: it removes selectable
    # text and can take minutes on image-heavy papers, so only run it when the
    # keep-text result cannot meet the target.
    with TemporaryDirectory(prefix="pdf_compress_") as temp_dir:
        original_size = source.stat().st_size
        temp = Path(temp_dir)
        candidates: list[tuple[Path, int, str]] = []

        optimized = temp / "optimized.pdf"
        try:
            optimize_pdf(source, optimized, config)
            optimized_size = optimized.stat().st_size
            if optimized_size <= original_size:
                candidates.append((optimized, optimized_size, "optimize"))
                if config.target_bytes is not None and optimized_size <= config.target_bytes:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(optimized, output)
                    return output
        except Exception as exc:
            logger.debug("Optimize PDF pass failed in auto mode: %s", exc)

        keep_text = temp / "keep_text.pdf"
        try:
            compress_pdf_keep_text(source, keep_text, config)
            keep_text_size = keep_text.stat().st_size
            if keep_text_size <= original_size:
                candidates.append((keep_text, keep_text_size, "text"))
                if config.target_bytes is not None and keep_text_size <= config.target_bytes:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(keep_text, output)
                    return output
        except Exception as exc:
            logger.debug("Keep-text PDF pass failed in auto mode: %s", exc)

        if config.target_bytes is None:
            # No hard size target: prefer the balanced keep-text result, then the
            # lossless optimization, and never rasterize implicitly.
            preferred = next((item for item in candidates if item[2] == "text"), None)
            best = preferred or (min(candidates, key=lambda item: item[1]) if candidates else None)
        else:
            # The keep-text pass could not hit the requested size. Rasterization is
            # now justified; honor the caller's explicit grayscale choice rather
            # than silently discarding color in auto mode.
            rasterized = temp / "rasterized.pdf"
            try:
                rasterize_pdf_to_target(source, rasterized, config)
                raster_size = rasterized.stat().st_size
                if raster_size <= original_size:
                    candidates.append((rasterized, raster_size, "raster"))
            except Exception as exc:
                logger.debug("Raster PDF pass failed in auto mode: %s", exc)

            under_target = [item for item in candidates if item[1] <= config.target_bytes]
            if under_target:
                # Quality order is deliberate: lossless > keep-text > raster.
                priority = {"optimize": 3, "text": 2, "raster": 1}
                best = max(under_target, key=lambda item: (priority[item[2]], item[1]))
            else:
                best = min(candidates, key=lambda item: item[1]) if candidates else None

        output.parent.mkdir(parents=True, exist_ok=True)
        if best is None:
            shutil.copy2(source, output)
        else:
            shutil.copy2(best[0], output)
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


# Near-lossless quality for the "smallest with minimal quality loss" pass.
_NEAR_LOSSLESS_QUALITY = 92

# Fidelity mode never rasterizes and never scales images down. It only chooses
# between lossless PDF optimization and near-lossless, same-resolution JPEG
# re-encodes that are actually smaller than the original image bytes.
_FIDELITY_IMAGE_QUALITIES = (95, _NEAR_LOSSLESS_QUALITY)


def compress_pdf_fidelity(source: Path, output: Path, config: CompressionConfig) -> Path:
    """Compress as much as possible without rasterizing or scaling images.

    Candidates are limited to lossless PDF optimization and same-resolution,
    near-lossless image re-encodes. The smallest result that is not larger than
    the source wins; if every pass fails, the source is copied unchanged.
    """
    with TemporaryDirectory(prefix="pdf_fidelity_") as temp_dir:
        original_size = source.stat().st_size
        temp = Path(temp_dir)
        candidates: list[tuple[Path, int, str]] = []

        optimized = temp / "optimized.pdf"
        try:
            optimize_pdf(source, optimized, config)
            optimized_size = optimized.stat().st_size
            if optimized_size <= original_size:
                candidates.append((optimized, optimized_size, "optimize"))
        except Exception as exc:
            logger.debug("Optimize PDF pass failed in fidelity mode: %s", exc)

        for quality in _FIDELITY_IMAGE_QUALITIES:
            candidate = temp / f"keeptext_q{quality}.pdf"
            try:
                _recompress_images_keep_text(
                    source,
                    candidate,
                    1.0,
                    quality,
                    config.strip_metadata,
                )
                candidate_size = candidate.stat().st_size
                if candidate_size <= original_size:
                    candidates.append((candidate, candidate_size, "text"))
            except Exception as exc:
                logger.debug(
                    "Keep-text PDF pass failed in fidelity mode at quality %d: %s",
                    quality,
                    exc,
                )

        chosen: Optional[tuple[Path, int, str]] = None
        if config.target_bytes is not None:
            under_target = [item for item in candidates if item[1] <= config.target_bytes]
            if under_target:
                chosen = next(
                    (item for item in under_target if item[2] == "optimize"),
                    None,
                ) or min(under_target, key=lambda item: item[1])
        if chosen is None and candidates:
            chosen = min(candidates, key=lambda item: item[1])

        output.parent.mkdir(parents=True, exist_ok=True)
        if chosen is None:
            shutil.copy2(source, output)
        else:
            shutil.copy2(chosen[0], output)
        return output


# (scale, quality) candidates for keep-text target search, ordered so the
# resulting file size is (roughly) descending — highest quality / largest first.
_KEEP_TEXT_CANDIDATES: list[tuple[float, int]] = [
    (1.0, 92), (1.0, 85), (1.0, 78), (1.0, 70),
    (0.85, 62), (0.72, 55), (0.60, 48), (0.50, 42),
    (0.42, 36), (0.34, 30), (0.28, 24),
]


def _recompress_images_keep_text(source: Path, output: Path, scale: float, quality: int, strip_metadata: bool) -> Path:
    """Re-encode embedded raster images at the given scale/quality while keeping
    all text, vectors and structure intact — the text layer is never rasterized.

    An image is only replaced when the re-encoded version is actually smaller,
    so already-efficient images are left untouched (avoids needless quality loss).
    """
    fitz = _fitz()
    quality = clamp_quality(quality)
    doc = fitz.open(source)
    try:
        processed: set[int] = set()
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                smask_xref = img[1]
                if xref in processed:
                    continue
                processed.add(xref)
                base = doc.extract_image(xref)
                if not base:
                    continue
                img_bytes = base["image"]
                w, h = base["width"], base["height"]
                if w < 50 or h < 50 or len(img_bytes) < 1024:
                    continue
                try:
                    with ExitStack() as stack:
                        image_source = stack.enter_context(io.BytesIO(img_bytes))
                        pil = stack.enter_context(Image.open(image_source))
                        pil = _prepare_pdf_image_for_jpeg(
                            pil,
                            stack,
                            doc=doc,
                            smask_xref=smask_xref,
                        )
                        dimensions_locked = _soft_mask_requires_matching_dimensions(
                            doc,
                            smask_xref,
                        )
                        if scale < 0.99 and not dimensions_locked:
                            nw = max(64, int(w * scale))
                            nh = max(64, int(h * scale))
                            if nw < w:
                                pil = stack.enter_context(
                                    pil.resize((nw, nh), Image.Resampling.LANCZOS)
                                )
                        with io.BytesIO() as buf:
                            pil.save(buf, format="JPEG", quality=quality, optimize=True)
                            new_bytes = buf.getvalue()
                    if len(new_bytes) < len(img_bytes):
                        _replace_pdf_image_preserving_soft_mask(
                            page,
                            doc,
                            xref=xref,
                            smask_xref=smask_xref,
                            stream=new_bytes,
                        )
                except _SoftMaskRestoreError:
                    raise
                except Exception as exc:
                    logger.debug("Skipping PDF image xref=%d during text-preserving compression: %s", xref, exc)
                    continue
        if strip_metadata:
            doc.set_metadata({})
            if hasattr(doc, "del_xml_metadata"):
                doc.del_xml_metadata()
        output.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output, garbage=4, deflate=True, clean=True)
        return output
    finally:
        doc.close()


def compress_pdf_keep_text(source: Path, output: Path, config: CompressionConfig) -> Path:
    """Keep-text PDF compression: the text layer is always preserved (never
    rasterized); only embedded raster images are re-encoded.

    Compression levels (when no target_bytes):
    - 1 (minimal): scale=1.0, quality=92 - near-lossless
    - 2 (balanced): scale=0.85, quality=78 - good balance (default)
    - 3 (aggressive): scale=0.60, quality=55 - smaller files
    - 4 (maximum): scale=0.34, quality=30 - smallest files

    With target_bytes: iterate candidates and stop at first that fits.
    """
    level = max(1, min(4, config.compression_level))
    level_params = {
        1: (1.0, 92),   # Minimal
        2: (0.85, 78),  # Balanced
        3: (0.60, 55),  # Aggressive
        4: (0.34, 30),  # Maximum
    }
    scale, quality = level_params[level]

    if config.target_bytes is None:
        return _recompress_images_keep_text(source, output, scale, quality, config.strip_metadata)

    target = config.target_bytes
    best_under: Optional[tuple[Path, int]] = None
    smallest: Optional[tuple[Path, int]] = None
    with TemporaryDirectory(prefix="pdf_keeptext_") as temp_dir:
        temp = Path(temp_dir)
        for index, (scale, quality) in enumerate(_KEEP_TEXT_CANDIDATES):
            candidate = temp / f"cand_{index}.pdf"
            _recompress_images_keep_text(source, candidate, scale, quality, config.strip_metadata)
            size = candidate.stat().st_size
            if smallest is None or size < smallest[1]:
                smallest = (candidate, size)
            if size <= target:
                best_under = (candidate, size)
                break
        chosen = best_under or smallest
        output.parent.mkdir(parents=True, exist_ok=True)
        if chosen is None:
            shutil.copy2(source, output)
        else:
            shutil.copy2(chosen[0], output)
    return output


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
