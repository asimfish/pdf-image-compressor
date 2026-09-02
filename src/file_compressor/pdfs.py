from __future__ import annotations

import io
import logging
import os
import re
import shutil
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, NamedTuple, Optional, Protocol

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


class _ImageRewriteError(RuntimeError):
    """Raised when an image object was partially rewritten and the document can
    no longer be saved without corrupting that image."""


class _SoftMaskRestoreError(_ImageRewriteError):
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
    xref: int = 0,
) -> Image.Image:
    """Return a JPEG-compatible base image without destroying PDF transparency.

    External PDF soft masks stay separate and are restored after replacement.
    Pillow-embedded alpha has no separate PDF object, so it is flattened to white.
    CMYK samples are converted through MuPDF when ``xref`` is given. Any derived
    images are owned by ``stack``.
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
    if image.mode == "CMYK":
        converted = _cmyk_to_rgb_via_mupdf(doc, xref, image.size) if xref else None
        if converted is not None:
            return stack.enter_context(converted)
        return stack.enter_context(image.convert("RGB"))
    if image.mode not in ("RGB", "L"):
        return stack.enter_context(image.convert("RGB"))
    return image


def _cmyk_to_rgb_via_mupdf(doc: object, xref: int, expected_size: tuple[int, int]) -> Optional[Image.Image]:
    """Decode a CMYK image through MuPDF's colour pipeline instead of Pillow's.

    Pillow's CMYK->RGB is the naive (1-C)(1-K) formula and lands ~10/255 away
    from what PDF viewers show for the same DeviceCMYK data; MuPDF's conversion
    (ICC-aware when the colourspace carries a profile) is measurably closer to
    Quartz, Ghostscript and MuPDF renderings of the original. Returns None when
    the document does not support pixmap extraction (test doubles, corrupt
    streams) so the caller can fall back to Pillow.
    """
    try:
        fitz = _fitz()
        pixmap = fitz.Pixmap(doc, xref)
        if pixmap.alpha:
            pixmap = fitz.Pixmap(pixmap, 0)
        if pixmap.n != 3:
            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
        if (pixmap.width, pixmap.height) != expected_size:
            return None
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    except Exception as exc:
        logger.debug("MuPDF CMYK conversion unavailable for xref=%d: %s", xref, exc)
        return None


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


# Colorspace families whose sample values are exactly what the JPEG encoder
# emits for the same component count. extract_image decodes every other family
# (Indexed palettes, Separation/DeviceN tints, Lab, Pattern) into base-space
# samples, so re-attaching the source entry to the re-encoded image would
# corrupt its colors.
_REENCODE_SAFE_COLORSPACE_FAMILIES = ("/ICCBased", "/CalRGB", "/CalGray")
_COLORSPACE_FAMILY_RE = re.compile(r"\s*\[?\s*(/[A-Za-z0-9]+)")


def _restorable_colorspace(
    doc: _PdfSoftMaskInspector,
    xref: int,
    source_components: Optional[int],
    encoded_mode: str,
) -> Optional[str]:
    """Return the raw /ColorSpace value to re-attach after re-encoding, or None.

    page.replace_image rebinds every replaced image to a generic sRGB profile.
    Wide-gamut sources (Display P3 scans, calibrated RGB) would render washed
    out, so the original entry is restored when it still describes the new
    samples: the family must be one whose values the encoder reproduces as-is
    and the component count must be unchanged (a CMYK->RGB flattening, for
    example, must keep the replacement default). Plain name entries
    (/DeviceRGB etc.) carry no profile worth preserving.
    """
    try:
        cs_type, cs_value = doc.xref_get_key(xref, "ColorSpace")
    except Exception:
        return None
    if cs_type not in ("xref", "array"):
        return None
    resolved = cs_value
    if cs_type == "xref":
        try:
            resolved = doc.xref_object(int(cs_value.split()[0]), compressed=True)
        except Exception:
            return None
    match = _COLORSPACE_FAMILY_RE.match(resolved or "")
    if match is None or match.group(1) not in _REENCODE_SAFE_COLORSPACE_FAMILIES:
        return None
    encoded_components = 3 if encoded_mode == "RGB" else 1
    if source_components != encoded_components:
        return None
    return cs_value


def _image_is_reencodable(doc: object, xref: int, extracted: dict) -> bool:
    """Reject images whose dictionary semantics would not survive a JPEG swap.

    Stencil masks (/ImageMask) are painted with the fill colour and colour-key
    or stencil /Mask entries refer to the original sample values, so both are
    left alone. A non-default /Decode array remaps the samples: when
    ``extract_image`` decoded the image itself (Indexed, CMYK-with-Decode,
    inverted gray) the remap is already baked into the pixels and the entry can
    be dropped, but when it passed the original stream through untouched (a
    DCT stream with an inverting Decode) re-encoding would flip the image.
    """
    try:
        mask_type, mask_value = doc.xref_get_key(xref, "ImageMask")  # type: ignore[attr-defined]
        if mask_type == "bool" and mask_value.lower() == "true":
            return False
        if doc.xref_get_key(xref, "Mask")[0] != "null":  # type: ignore[attr-defined]
            return False
        decode_type, decode_value = doc.xref_get_key(xref, "Decode")  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug("Unable to inspect image dictionary for xref=%d: %s", xref, exc)
        return False
    if decode_type == "null":
        return True
    components = extracted.get("colorspace")
    if decode_type != "array" or not components:
        return False
    try:
        values = [float(token) for token in decode_value.strip("[]").split()]
    except ValueError:
        return False
    default = [component for _ in range(components) for component in (0.0, 1.0)]
    if values == default:
        return True
    try:
        raw = doc.xref_stream_raw(xref)  # type: ignore[attr-defined]
    except Exception:
        return False
    return raw != extracted.get("image")


# Image dictionary entries that stay meaningful after the sample data has been
# re-encoded and are carried over from the original image: rendering hints,
# optional-content membership and tagged-PDF structure links.
_CARRIED_IMAGE_KEYS: dict[str, tuple[str, ...]] = {
    "Interpolate": ("bool",),
    "Intent": ("name",),
    "OC": ("xref",),
    "StructParent": ("int",),
}


def _snapshot_carried_image_keys(doc: _PdfSoftMaskInspector, xref: int) -> dict[str, str]:
    carried: dict[str, str] = {}
    for key, accepted_types in _CARRIED_IMAGE_KEYS.items():
        try:
            value_type, value = doc.xref_get_key(xref, key)
        except Exception:
            continue
        if value_type in accepted_types:
            carried[key] = value
    return carried


def _delete_xref_keys(doc: object, xref: int, keys: list[str]) -> None:
    """Remove dictionary entries outright.

    ``Document.xref_set_key(xref, key, "null")`` serialises a literal ``null``
    instead of dropping the entry, so the low-level MuPDF binding is used.
    """
    fitz = _fitz()
    pdf = fitz.mupdf.pdf_document_from_fz_document(doc.this)  # type: ignore[attr-defined]
    obj = fitz.mupdf.pdf_load_object(pdf, xref)
    for key in keys:
        fitz.mupdf.pdf_dict_dels(obj, key)


def _sanitize_replaced_image_dict(doc: object, xref: int, carried: dict[str, str]) -> None:
    """Repair the image dictionary that ``page.replace_image`` leaves behind.

    replace_image copies the new image object over the old xref and writes a
    literal ``null`` for every key the old dictionary had but the new one lacks
    (/Interpolate, /Intent, /Name, ...). Ghostscript type-checks these entries
    (``/Interpolate null`` is not a boolean) and silently drops the whole image,
    rendering a blank page. Null entries are removed and the entries that still
    apply to the re-encoded samples are restored from the original.
    """
    get_keys = getattr(doc, "xref_get_keys", None)
    if get_keys is None:
        return
    try:
        null_keys = [
            key
            for key in get_keys(xref)
            if doc.xref_get_key(xref, key)[0] == "null"  # type: ignore[attr-defined]
        ]
        if null_keys:
            _delete_xref_keys(doc, xref, null_keys)
        for key, value in carried.items():
            doc.xref_set_key(xref, key, value)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("Could not sanitize image dictionary for xref=%d: %s", xref, exc)


def _replace_pdf_image_preserving_soft_mask(
    page: _PdfImagePage,
    doc: _PdfImageUpdater,
    *,
    xref: int,
    smask_xref: int,
    stream: bytes,
    colorspace_raw: Optional[str] = None,
) -> None:
    carried = _snapshot_carried_image_keys(doc, xref) if hasattr(doc, "xref_get_key") else {}
    page.replace_image(xref, stream=stream)
    _sanitize_replaced_image_dict(doc, xref, carried)
    if colorspace_raw:
        # replace_image rebinds the image to PyMuPDF's generic sRGB profile;
        # restore the document's original ICC colorspace so wide-gamut scans
        # (e.g. Display P3 notes exports) keep their colors after compression.
        try:
            doc.xref_set_key(xref, "ColorSpace", colorspace_raw)
        except Exception:
            logger.warning(
                "Could not restore original ColorSpace %r for image xref=%d",
                colorspace_raw,
                xref,
            )
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
                if not _image_is_reencodable(doc, xref, base_image):
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
                            xref=xref,
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
                        colorspace_raw = _restorable_colorspace(
                            doc, xref, base_image.get("colorspace"), pil_img.mode
                        )
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
                            colorspace_raw=colorspace_raw,
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

    # Auto mode is quality-first. Preserve the original text/vector layer and
    # return the closest lossless/keep-text result even when it slightly misses
    # the target. Full-page rasterization is destructive, so it only runs when
    # the caller explicitly chooses raster mode.
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
            under_target = [item for item in candidates if item[1] <= config.target_bytes]
            if under_target:
                # Quality order is deliberate: lossless > keep-text. Auto never
                # rasterizes implicitly; explicit raster mode remains available.
                priority = {"optimize": 2, "text": 1}
                best = max(under_target, key=lambda item: (priority[item[2]], item[1]))
            else:
                # A keep-text/lossless result that only slightly misses the target is
                # far more useful than destroying the text layer. Return the closest
                # vector result and let the caller surface best_over_target.
                overshoot_limit = int(config.target_bytes * 1.20)
                near_vector = [item for item in candidates if item[1] <= overshoot_limit]
                if near_vector:
                    priority = {"optimize": 2, "text": 1}
                    best = max(near_vector, key=lambda item: (priority[item[2]], -item[1]))
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

# Without a byte budget, fidelity mode never scales images down. It only
# chooses between lossless PDF optimization and near-lossless, same-resolution
# JPEG re-encodes that are actually smaller than the original image bytes.
_FIDELITY_IMAGE_QUALITIES = (95, _NEAR_LOSSLESS_QUALITY)


def compress_pdf_fidelity(source: Path, output: Path, config: CompressionConfig) -> Path:
    """Quality-first compression that never rasterizes pages.

    Without a target, candidates are limited to lossless PDF optimization and
    same-resolution, near-lossless image re-encodes; the smallest result that is
    not larger than the source wins, and the source is copied unchanged if every
    pass fails.

    With a target, the byte budget is a hard constraint: the lossless pass wins
    when it fits, otherwise the keep-text quality ladder is walked from q95 down
    (with binary refinement between rungs) and the highest quality that fits is
    returned. Only when even the lowest rung misses the budget does the closest
    text-preserving result come back, so callers can surface best_over_target.
    """
    with TemporaryDirectory(prefix="pdf_fidelity_") as temp_dir:
        original_size = source.stat().st_size
        temp = Path(temp_dir)
        output.parent.mkdir(parents=True, exist_ok=True)

        optimized: Optional[tuple[Path, int]] = None
        optimized_path = temp / "optimized.pdf"
        try:
            optimize_pdf(source, optimized_path, config)
            optimized_size = optimized_path.stat().st_size
            if optimized_size <= original_size:
                optimized = (optimized_path, optimized_size)
        except Exception as exc:
            logger.debug("Optimize PDF pass failed in fidelity mode: %s", exc)

        if config.target_bytes is not None:
            if optimized is not None and optimized[1] <= config.target_bytes:
                shutil.copy2(optimized[0], output)
                return output
            best_under, attempts = _search_keep_text_under_target(
                source,
                temp,
                config.target_bytes,
                config.strip_metadata,
                _FIDELITY_LADDER,
            )
            if best_under is not None:
                shutil.copy2(best_under.path, output)
                return output
            # Even the lowest rung misses the budget, so downscaling bought
            # nothing: hand back the smallest same-resolution result instead of
            # a needlessly degraded one and let the caller flag over-target.
            full_res = [item for item in attempts if item.native_resolution]
            fallback = min(full_res or attempts, key=lambda item: item.size) if attempts else None
            if fallback is not None:
                shutil.copy2(fallback.path, output)
            elif optimized is not None:
                shutil.copy2(optimized[0], output)
            else:
                shutil.copy2(source, output)
            return output

        candidates: list[tuple[Path, int]] = [optimized] if optimized else []
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
                    candidates.append((candidate, candidate_size))
            except Exception as exc:
                logger.debug(
                    "Keep-text PDF pass failed in fidelity mode at quality %d: %s",
                    quality,
                    exc,
                )
        chosen = min(candidates, key=lambda item: item[1]) if candidates else None
        shutil.copy2(chosen[0] if chosen else source, output)
        return output


class _Rung(NamedTuple):
    """One step of a keep-text quality ladder.

    ``scale`` is a uniform resize factor (1.0 = keep pixels). ``max_dpi`` caps
    the effective resolution of each image relative to its largest placement on
    the page: only oversampled images shrink, images already at or below the
    cap are left at native resolution.
    """

    scale: float
    quality: int
    max_dpi: Optional[int] = None


# Keep-text target search ladder, ordered so the resulting file size is
# (roughly) descending. Quality drops first at native resolution; from q70 on,
# each rung also tightens a placement-aware DPI cap so that budget is recovered
# from oversampled images (a 4000 px figure printed 5 in wide is 800 DPI; a
# 300 DPI cap is invisible on screen or paper) instead of from a uniform
# downscale that blurs small images displayed at their native size.
_KEEP_TEXT_CANDIDATES: list[_Rung] = [
    _Rung(1.0, 92), _Rung(1.0, 85), _Rung(1.0, 78), _Rung(1.0, 70),
    _Rung(1.0, 66, 300), _Rung(1.0, 62, 250), _Rung(1.0, 58, 200), _Rung(1.0, 54, 170),
    _Rung(1.0, 50, 150), _Rung(1.0, 45, 130), _Rung(1.0, 40, 110), _Rung(1.0, 35, 96),
    _Rung(1.0, 30, 80), _Rung(1.0, 24, 72),
]

# Fidelity mode starts one near-lossless rung higher so a generous budget is
# spent on quality instead of settling at q92.
_FIDELITY_LADDER: list[_Rung] = [_Rung(1.0, 95), *_KEEP_TEXT_CANDIDATES]


def _image_effective_dpi(doc: object) -> dict[int, float]:
    """Return, per image xref, the effective DPI of its most demanding placement.

    An image drawn at several sizes needs enough pixels for the largest one, so
    the lowest DPI across placements is kept. Images that are not drawn on any
    page (or whose placement cannot be read) are absent and never downscaled.
    """
    fitz = _fitz()
    dpi: dict[int, float] = {}
    for page in doc:  # type: ignore[attr-defined]
        try:
            infos = page.get_image_info(xrefs=True)
        except Exception as exc:
            logger.debug("Unable to read image placements on page %s: %s", page.number, exc)
            continue
        for info in infos:
            xref = int(info.get("xref", 0) or 0)
            width = int(info.get("width", 0) or 0)
            height = int(info.get("height", 0) or 0)
            if not xref or width <= 0 or height <= 0:
                continue
            rect = fitz.Rect(info["bbox"])
            if rect.width <= 0 or rect.height <= 0:
                continue
            effective = max(width / (rect.width / 72.0), height / (rect.height / 72.0))
            dpi[xref] = min(dpi.get(xref, float("inf")), effective)
    return dpi


class _CachedImage(NamedTuple):
    """One re-encodable image, extracted once per document and reused per rung."""

    xref: int
    smask_xref: int
    original_len: int
    data: bytes  # compressed source bytes (or PNG for images pre-converted on the main thread)
    width: int
    height: int
    flatten_alpha: bool  # Pillow-embedded alpha with no PDF soft mask: flatten to white
    dimensions_locked: bool  # /Matte soft mask: base must keep its pixel dimensions
    placed_dpi: Optional[float]
    colorspace_raw: Optional[str]  # original /ColorSpace to keep (None -> Device*)
    encoded_mode: str  # "L" or "RGB"
    graphic: bool = False  # line art / plots: palette PNG instead of JPEG


class _EncodedImage(NamedTuple):
    """A re-encoded sample stream plus the dictionary entries describing it."""

    xref: int
    data: bytes
    width: int
    height: int
    filter: str = "/DCTDecode"
    bits_per_component: int = 8
    decode_parms: Optional[str] = None
    colorspace: Optional[str] = None  # explicit entry (Indexed palette); None -> caller decides


# Pillow releases the GIL while decoding, resampling and JPEG-encoding, so a
# small thread pool gives near-linear speedups on image-heavy documents.
_ENCODE_WORKERS = max(2, min(8, os.cpu_count() or 4))

# Resampling by less than this factor trades interpolation blur and a shifted
# pixel grid for a byte saving too small to matter (a 0.94x resize saves ~12%
# while dropping one JPEG quality point saves about as much); such images keep
# their native pixels.
_MAX_KEEP_NATIVE_SCALE = 0.85


# Small images with at most this many distinct colours are treated as flat
# graphics (charts, logos, thumbnails) and left in their lossless encoding:
# JPEG rings on their hard edges and the byte saving is negligible.
_FLAT_GRAPHIC_MAX_COLORS = 256
# Above this size a low-colour image is a page scan or a large screenshot,
# where JPEG re-encoding is the only way to meet a budget and the saving is
# substantial; those stay eligible.
_FLAT_GRAPHIC_MAX_PIXELS = 1_000_000


def _is_flat_graphic(image: Image.Image) -> bool:
    if image.width * image.height > _FLAT_GRAPHIC_MAX_PIXELS:
        return False
    probe = image if image.mode in ("RGB", "L") else image.convert("RGB")
    try:
        return probe.getcolors(maxcolors=_FLAT_GRAPHIC_MAX_COLORS) is not None
    finally:
        if probe is not image:
            probe.close()


# Graphic-like images (plots, diagrams, line art rendered to PNG) are mostly
# one background colour with thin anti-aliased strokes. JPEG smears their
# chroma and rings on every edge; a palette PNG is smaller *and* near-lossless
# for them (measured on vector-field figures: 0.5x palette PNG scored SSIM 0.98
# at 673 KB where JPEG needed 1.1 MB for 0.90).
_GRAPHIC_MIN_BACKGROUND_FRACTION = 0.5
_GRAPHIC_MAX_THUMB_COLORS = 3000
_GRAPHIC_MAX_PIXELS = 1_200_000  # page scans are larger and need JPEG to meet a budget
_GRAPHIC_THUMB = 128
# Sparse line art compresses so well as PNG that it can afford twice the
# resolution photos get at the same rung.
_GRAPHIC_DPI_RELIEF = 2.0


def _is_graphic_like(image: Image.Image) -> bool:
    if image.width * image.height > _GRAPHIC_MAX_PIXELS:
        return False
    thumb = image.convert("RGB") if image.mode != "RGB" else image.copy()
    try:
        thumb.thumbnail((_GRAPHIC_THUMB, _GRAPHIC_THUMB), Image.Resampling.NEAREST)
        colors = thumb.getcolors(maxcolors=_GRAPHIC_MAX_THUMB_COLORS)
        if colors is None:
            return False
        dominant = max(count for count, _ in colors)
        return dominant / (thumb.width * thumb.height) >= _GRAPHIC_MIN_BACKGROUND_FRACTION
    finally:
        thumb.close()


def _png_chunks(data: bytes) -> dict[bytes, bytes]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG stream")
    chunks: dict[bytes, bytes] = {}
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        chunks[kind] = chunks.get(kind, b"") + data[pos + 8 : pos + 8 + length]
        pos += 12 + length
    return chunks


def _encode_graphic_png(pil: Image.Image, xref: int, base_colorspace: Optional[str]) -> _EncodedImage:
    """Encode line art as a palette (or grayscale) PNG and repackage its IDAT
    payload as a PDF Flate stream with PNG predictors, byte-for-byte as
    compact as the PNG itself."""
    with ExitStack() as stack:
        if pil.mode == "L":
            encoded = pil
        else:
            rgb = pil if pil.mode == "RGB" else stack.enter_context(pil.convert("RGB"))
            exact = rgb.getcolors(maxcolors=256)
            encoded = stack.enter_context(
                rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=len(exact) if exact else 256)
            )
        with io.BytesIO() as buf:
            encoded.save(buf, format="PNG", optimize=True)
            chunks = _png_chunks(buf.getvalue())
    width, height, bit_depth, color_type = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    if color_type == 3:
        palette = chunks[b"PLTE"]
        base = base_colorspace or "/DeviceRGB"
        colorspace = f"[/Indexed {base} {len(palette) // 3 - 1} <{palette.hex()}>]"
    elif color_type == 0:
        colorspace = base_colorspace or "/DeviceGray"
    else:
        raise ValueError(f"unexpected PNG colour type {color_type}")
    parms = f"<</Predictor 15/Colors 1/BitsPerComponent {bit_depth}/Columns {width}>>"
    return _EncodedImage(
        xref, chunks[b"IDAT"], width, height, "/FlateDecode", bit_depth, parms, colorspace
    )


def _pil_encoded_mode(image: Image.Image, flatten_alpha: bool) -> str:
    if flatten_alpha:
        return "RGB"
    return "L" if image.mode == "L" else "RGB"


class _KeepTextSession:
    """Keep-text re-encoding of one document, shared across ladder rungs.

    Image extraction, placement analysis and colourspace decisions are made once
    (they are identical for every rung and dominate the runtime through
    PyMuPDF); each rung then only decodes, resamples and JPEG-encodes the cached
    bytes on a thread pool and rewrites the image objects in a fresh copy of the
    source document.
    """

    def __init__(self, source: Path, strip_metadata: bool) -> None:
        self.source = source
        self.strip_metadata = strip_metadata
        self.images: list[_CachedImage] = []
        fitz = _fitz()
        doc = fitz.open(source)
        try:
            effective_dpi = _image_effective_dpi(doc)
            seen: set[int] = set()
            for page in doc:
                for img in page.get_images(full=True):
                    xref, smask_xref = img[0], img[1]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    cached = self._extract(doc, xref, smask_xref, effective_dpi.get(xref))
                    if cached is not None:
                        self.images.append(cached)
        finally:
            doc.close()

    @staticmethod
    def _extract(
        doc: object, xref: int, smask_xref: int, placed_dpi: Optional[float]
    ) -> Optional[_CachedImage]:
        base = doc.extract_image(xref)  # type: ignore[attr-defined]
        if not base:
            return None
        data = base["image"]
        width, height = base["width"], base["height"]
        if width < 50 or height < 50 or len(data) < 1024:
            return None
        if not _image_is_reencodable(doc, xref, base):
            return None
        try:
            with ExitStack() as stack:
                pil = stack.enter_context(Image.open(io.BytesIO(data)))
                # Validates the soft mask (raises for unusable masks) and tells
                # us whether Pillow-level alpha has to be flattened.
                prepared = _prepare_pdf_image_for_jpeg(
                    pil,
                    stack,
                    doc=doc,  # type: ignore[arg-type]
                    smask_xref=smask_xref,
                    xref=xref,
                )
                if base["ext"] != "jpeg" and _is_flat_graphic(prepared):
                    # Plots, logos and screenshots: JPEG rings on their hard
                    # edges and saves next to nothing over the lossless source.
                    logger.debug("Keeping flat-colour image xref=%d lossless", xref)
                    return None
                graphic = base["ext"] != "jpeg" and _is_graphic_like(prepared)
                flatten_alpha = smask_xref <= 0 and (
                    "A" in pil.getbands() or (pil.mode == "P" and "transparency" in pil.info)
                )
                if pil.mode == "CMYK" and prepared is not pil:
                    # Colour conversion needs the document (MuPDF pipeline), so it
                    # is done once here; workers receive lossless RGB bytes.
                    with io.BytesIO() as buf:
                        prepared.save(buf, format="PNG", compress_level=1)
                        data = buf.getvalue()
                    flatten_alpha = False
                encoded_mode = _pil_encoded_mode(prepared, flatten_alpha)
                dimensions_locked = _soft_mask_requires_matching_dimensions(
                    doc,  # type: ignore[arg-type]
                    smask_xref,
                )
                colorspace_raw = _restorable_colorspace(
                    doc,  # type: ignore[arg-type]
                    xref,
                    base.get("colorspace"),
                    encoded_mode,
                )
        except Exception as exc:
            logger.debug("Skipping PDF image xref=%d during text-preserving compression: %s", xref, exc)
            return None
        return _CachedImage(
            xref=xref,
            smask_xref=smask_xref,
            original_len=len(base["image"]),
            data=data,
            width=width,
            height=height,
            flatten_alpha=flatten_alpha,
            dimensions_locked=dimensions_locked,
            placed_dpi=placed_dpi,
            colorspace_raw=colorspace_raw,
            encoded_mode=encoded_mode,
            graphic=graphic,
        )

    def encode(self, output: Path, scale: float, quality: int, max_dpi: Optional[int] = None) -> Path:
        """Write ``output`` with every cached image re-encoded for one rung."""
        fitz = _fitz()
        quality = clamp_quality(quality)
        with ThreadPoolExecutor(max_workers=_ENCODE_WORKERS) as pool:
            encoded = list(
                pool.map(lambda item: _encode_cached_image(item, scale, quality, max_dpi), self.images)
            )
        doc = fitz.open(self.source)
        try:
            for item, result in zip(self.images, encoded):
                if result is None or len(result.data) >= item.original_len:
                    continue
                try:
                    _rewrite_image_object(doc, item, result)
                except _ImageRewriteError:
                    raise
                except Exception as exc:
                    # The stream may already have been swapped, so the object
                    # cannot be trusted anymore: abort instead of saving a
                    # document with a corrupt image.
                    logger.error("PDF image xref=%d could not be rewritten: %s", item.xref, exc)
                    raise _ImageRewriteError(f"Unable to rewrite PDF image xref {item.xref}") from exc
            if self.strip_metadata:
                doc.set_metadata({})
                if hasattr(doc, "del_xml_metadata"):
                    doc.del_xml_metadata()
            output.parent.mkdir(parents=True, exist_ok=True)
            doc.save(output, garbage=4, deflate=True, clean=True)
            return output
        finally:
            doc.close()


def _encode_cached_image(
    item: _CachedImage, scale: float, quality: int, max_dpi: Optional[int]
) -> Optional[_EncodedImage]:
    """Decode, resample and JPEG-encode one cached image (pure Pillow, thread-safe)."""
    image_scale = scale
    cap = max_dpi * _GRAPHIC_DPI_RELIEF if (max_dpi and item.graphic) else max_dpi
    if cap and item.placed_dpi and item.placed_dpi > cap:
        image_scale = min(image_scale, cap / item.placed_dpi)
    try:
        with ExitStack() as stack:
            pil = stack.enter_context(Image.open(io.BytesIO(item.data)))
            if item.flatten_alpha:
                rgba = stack.enter_context(pil.convert("RGBA"))
                background = stack.enter_context(Image.new("RGB", pil.size, "white"))
                background.paste(rgba, mask=rgba.getchannel("A"))
                pil = background
            if pil.mode != item.encoded_mode:
                pil = stack.enter_context(pil.convert(item.encoded_mode))
            width, height = pil.size
            if image_scale <= _MAX_KEEP_NATIVE_SCALE and not item.dimensions_locked:
                new_w = max(64, int(width * image_scale))
                new_h = max(64, int(height * image_scale))
                if new_w < width:
                    pil = stack.enter_context(pil.resize((new_w, new_h), Image.Resampling.LANCZOS))
                    width, height = pil.size
            if item.graphic:
                return _encode_graphic_png(pil, item.xref, item.colorspace_raw)
            with io.BytesIO() as buf:
                pil.save(buf, format="JPEG", quality=quality, optimize=True)
                return _EncodedImage(item.xref, buf.getvalue(), width, height)
    except Exception as exc:
        logger.debug("Skipping PDF image xref=%d during encoding: %s", item.xref, exc)
        return None


def _rewrite_image_object(doc: object, item: _CachedImage, result: _EncodedImage) -> None:
    """Swap the sample data of an existing image XObject in place.

    Unlike ``page.replace_image`` this keeps the dictionary the document author
    wrote (/SMask, /Interpolate, /Intent, /OC, /StructParent, an ICC
    /ColorSpace that still applies) and never leaves ``null`` placeholders that
    Ghostscript rejects. Only the entries describing the sample format are
    rewritten for the new JPEG data.
    """
    xref = item.xref
    doc.update_stream(xref, result.data, new=True, compress=0)  # type: ignore[attr-defined]
    doc.xref_set_key(xref, "Filter", result.filter)  # type: ignore[attr-defined]
    doc.xref_set_key(xref, "Width", str(result.width))  # type: ignore[attr-defined]
    doc.xref_set_key(xref, "Height", str(result.height))  # type: ignore[attr-defined]
    doc.xref_set_key(xref, "BitsPerComponent", str(result.bits_per_component))  # type: ignore[attr-defined]
    if result.colorspace is not None:
        doc.xref_set_key(xref, "ColorSpace", result.colorspace)  # type: ignore[attr-defined]
    elif item.colorspace_raw is None:
        device = "/DeviceGray" if item.encoded_mode == "L" else "/DeviceRGB"
        doc.xref_set_key(xref, "ColorSpace", device)  # type: ignore[attr-defined]
    stale = [
        key
        for key in ("Decode", "DecodeParms")
        if doc.xref_get_key(xref, key)[0] != "null"  # type: ignore[attr-defined]
    ]
    if stale:
        _delete_xref_keys(doc, xref, stale)
    if result.decode_parms is not None:
        doc.xref_set_key(xref, "DecodeParms", result.decode_parms)  # type: ignore[attr-defined]


def _recompress_images_keep_text(
    source: Path,
    output: Path,
    scale: float,
    quality: int,
    strip_metadata: bool,
    max_dpi: Optional[int] = None,
) -> Path:
    """Re-encode embedded raster images at the given scale/quality while keeping
    all text, vectors and structure intact — the text layer is never rasterized.

    ``scale`` resizes every image uniformly; ``max_dpi`` additionally shrinks
    images whose effective resolution on the page exceeds the cap. An image is
    only replaced when the re-encoded version is actually smaller, so
    already-efficient images are left untouched (avoids needless quality loss).
    """
    return _KeepTextSession(source, strip_metadata).encode(output, scale, quality, max_dpi)


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

    with TemporaryDirectory(prefix="pdf_keeptext_") as temp_dir:
        best_under, attempts = _search_keep_text_under_target(
            source,
            Path(temp_dir),
            config.target_bytes,
            config.strip_metadata,
            _KEEP_TEXT_CANDIDATES,
        )
        chosen = best_under or _closest_without_waste(attempts)
        output.parent.mkdir(parents=True, exist_ok=True)
        if chosen is None:
            shutil.copy2(source, output)
        else:
            shutil.copy2(chosen.path, output)
    return output


class _KeepTextAttempt(NamedTuple):
    path: Path
    size: int
    rung: _Rung

    @property
    def native_resolution(self) -> bool:
        return self.rung.scale >= 0.99 and self.rung.max_dpi is None


# When no rung reaches the budget, a lower rung that saves only a few percent
# more is not worth its quality loss; the search settles for the best rung
# within this factor of the smallest achievable size.
_UNREACHABLE_SIZE_TOLERANCE = 1.10


def _closest_without_waste(attempts: list[_KeepTextAttempt]) -> Optional[_KeepTextAttempt]:
    """Pick the fallback when every ladder rung misses the target.

    The budget cannot be met without rasterizing, so the remaining bytes are
    dominated by content the ladder cannot shrink (vector art, fonts, text).
    Returns the highest-quality attempt whose size is within
    ``_UNREACHABLE_SIZE_TOLERANCE`` of the smallest one.
    """
    if not attempts:
        return None
    smallest = min(attempts, key=lambda item: item.size)
    eligible = [item for item in attempts if item.size <= smallest.size * _UNREACHABLE_SIZE_TOLERANCE]
    return max(
        eligible,
        key=lambda item: (item.rung.quality, item.rung.max_dpi or float("inf"), item.rung.scale),
    )


def _search_keep_text_under_target(
    source: Path,
    temp: Path,
    target: int,
    strip_metadata: bool,
    ladder: list[_Rung],
) -> tuple[Optional[_KeepTextAttempt], list[_KeepTextAttempt]]:
    """Walk a quality ladder from highest quality down.

    Returns ``(best_under_target, attempts)``: the highest-quality candidate
    that fits the budget (or None) and every ladder rung that was encoded, so
    callers can pick a fallback when nothing fits.

    The coarse ladder jumps several JPEG quality points (and, on the lower
    rungs, a resolution step) per rung, so the first fitting rung can leave a
    lot of budget unused (e.g. picking q78 when q82 still fits). After the
    first fit, binary-search the gap up to the rung that failed. Encoded size is
    near-monotonic along the segment joining the two rungs and every accepted
    probe is verified against the target, so the refined result never
    overshoots the budget.
    """
    attempts: list[_KeepTextAttempt] = []
    encoded: dict[int, _KeepTextAttempt] = {}
    session = _KeepTextSession(source, strip_metadata)

    def encode(index: int) -> _KeepTextAttempt:
        if index not in encoded:
            rung = ladder[index]
            path = temp / f"cand_{index}.pdf"
            session.encode(path, rung.scale, rung.quality, rung.max_dpi)
            encoded[index] = _KeepTextAttempt(path, path.stat().st_size, rung)
            attempts.append(encoded[index])
        return encoded[index]

    fit_index = _first_fitting_rung(len(ladder), target, lambda i: encode(i).size)
    best_under = encoded[fit_index] if fit_index is not None else None
    if best_under is not None and fit_index is not None:
        fit = ladder[fit_index]
        if fit_index:
            over: Optional[_Rung] = ladder[fit_index - 1]
        elif fit.quality < _MAX_JPEG_QUALITY:
            # The top rung already fits: spend the remaining budget on quality
            # up to the encoder ceiling instead of settling for the rung.
            over = _Rung(fit.scale, _MAX_JPEG_QUALITY + 1, fit.max_dpi)
        else:
            over = None
        if over is not None:

            def probe(rung: _Rung) -> _KeepTextAttempt:
                dpi_tag = f"_d{rung.max_dpi}" if rung.max_dpi else ""
                path = temp / f"cand_refine_s{int(round(rung.scale * 1000)):04d}_q{rung.quality}{dpi_tag}.pdf"
                session.encode(path, rung.scale, rung.quality, rung.max_dpi)
                return _KeepTextAttempt(path, path.stat().st_size, rung)

            refined = _refine_between_rungs(fit, over, target, probe)
            if refined is not None:
                best_under = refined
    return best_under, attempts


def _first_fitting_rung(count: int, target: int, size_of: Callable[[int], int]) -> Optional[int]:
    """Locate the highest-quality ladder rung whose encoded size fits ``target``.

    Rung sizes decrease (near-)monotonically with the index, so instead of
    encoding every rung from the top, gallop downward (indices 0, 1, 3, 7, ...)
    until a rung fits, then bisect the last gap. Both the failing neighbour and
    the fit are encoded, which is exactly what the follow-up refinement needs.
    Returns None when even the last rung is too large.
    """
    if count == 0:
        return None
    if size_of(0) <= target:
        return 0
    low = 0  # highest index known to fail
    step = 1
    high: Optional[int] = None  # lowest index known to fit
    while high is None:
        candidate = low + step
        if candidate >= count:
            candidate = count - 1
        if size_of(candidate) <= target:
            high = candidate
        elif candidate == count - 1:
            return None
        else:
            low = candidate
            step *= 2
    while high - low > 1:
        mid = (low + high) // 2
        if size_of(mid) <= target:
            high = mid
        else:
            low = mid
    return high


# JPEG quality ceiling for budget searches. Above 95 libjpeg's quantization
# tables approach unity and file size balloons for no visible gain.
_MAX_JPEG_QUALITY = 95


# Number of bisection steps along a mixed scale/DPI/quality gap. Four steps
# resolve the segment to 1/16 while costing about as much as the pure-quality
# search.
_MIXED_REFINEMENT_STEPS = 4


def _refine_between_rungs(
    fit: _Rung,
    over: _Rung,
    target: int,
    probe: Callable[[_Rung], _KeepTextAttempt],
) -> Optional[_KeepTextAttempt]:
    """Bisect from ``fit`` (known to satisfy the budget) toward ``over`` (known
    to exceed it) and return the largest probed result that still fits.

    Rungs that differ only in quality bisect the integer quality interval. Rungs
    that also differ in scale or DPI cap bisect the parameter ``t`` of the
    segment between them, so the resolution step is refined together with the
    quality step instead of leaving the whole gap unused.
    """
    best: Optional[_KeepTextAttempt] = None

    def consider(attempt: _KeepTextAttempt) -> bool:
        nonlocal best
        if attempt.size > target:
            return False
        if best is None or attempt.size > best.size:
            best = attempt
        return True

    def bisect_quality(scale: float, max_dpi: Optional[int], low: int, high: int) -> None:
        while low <= high:
            mid = (low + high) // 2
            if consider(probe(_Rung(scale, mid, max_dpi))):
                low = mid + 1
            else:
                high = mid - 1

    same_scale = abs(over.scale - fit.scale) < 1e-9
    if same_scale and over.max_dpi == fit.max_dpi:
        bisect_quality(fit.scale, fit.max_dpi, fit.quality + 1, over.quality - 1)
        return best

    # The rungs also differ in resolution. Pixels are worth more than JPEG
    # quality points at the same byte cost (resampling blurs and shifts the
    # grid, while q66 vs q69 is invisible), so first try to stay at the
    # failing rung's resolution with a lower quality; only when no such
    # quality fits is the resolution step refined together with the quality.
    bisect_quality(over.scale, over.max_dpi, fit.quality, over.quality - 1)
    if best is not None:
        return best

    # A rung without a cap leaves oversampled images untouched; approximate
    # that end of the segment with twice the fitting cap so the DPI axis can be
    # interpolated. The endpoint itself is never probed, so the approximation
    # only shapes the path of the bisection.
    fit_dpi = fit.max_dpi
    over_dpi = over.max_dpi if over.max_dpi is not None else (fit_dpi * 2 if fit_dpi else None)
    low_t, high_t = 0.0, 1.0
    tried: set[_Rung] = set()
    for _ in range(_MIXED_REFINEMENT_STEPS):
        mid_t = (low_t + high_t) / 2
        scale = round(fit.scale + mid_t * (over.scale - fit.scale), 4)
        quality = int(round(fit.quality + mid_t * (over.quality - fit.quality)))
        max_dpi = (
            int(round(fit_dpi + mid_t * (over_dpi - fit_dpi)))
            if fit_dpi is not None and over_dpi is not None
            else fit_dpi
        )
        rung = _Rung(scale, quality, max_dpi)
        if rung in tried:
            break
        tried.add(rung)
        if consider(probe(rung)):
            low_t = mid_t
        else:
            high_t = mid_t
    return best


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
