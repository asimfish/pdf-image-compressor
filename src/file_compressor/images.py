from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from .models import CompressionConfig
from .utils import clamp_quality


def output_suffix_for_image(source: Path, config: CompressionConfig) -> str:
    suffix = source.suffix.lower()
    if config.to_webp:
        return ".webp"
    if suffix in {".bmp", ".tif", ".tiff"}:
        return ".jpg"
    return suffix


def compress_image(source: Path, output: Path, config: CompressionConfig) -> Path:
    qualities = _quality_candidates(config.quality) if config.target_bytes else [config.quality]
    edges = _edge_candidates(config.max_edge) if config.target_bytes else [config.max_edge]
    suffix = output.suffix.lower()
    best_data: Optional[bytes] = None
    best_size: Optional[int] = None
    under_target_data: Optional[bytes] = None
    under_target_size: Optional[int] = None

    try:
        raw = ImageOps.exif_transpose(Image.open(source))
    except UnidentifiedImageError as exc:
        raise RuntimeError(f"Unsupported or corrupt image: {source}") from exc
    # Must be set before the try below so the finally never sees it unbound
    # if EXIF handling raises (e.g. malformed EXIF with strip_metadata=False).
    raw_closed = False
    try:
        if not config.strip_metadata:
            exif_obj = raw.getexif()
            if 0x0112 in exif_obj:
                del exif_obj[0x0112]
            exif_bytes = exif_obj.tobytes() if exif_obj else None
        else:
            exif_bytes = None
        normalized = _normalize_mode(raw, suffix)
        if normalized is not raw:
            raw.close()
            raw_closed = True
        try:
            if config.target_bytes and None in edges:
                pixels = normalized.width * normalized.height
                if pixels > config.target_bytes * 100:
                    edges = [e for e in edges if e is not None]
            first_combo = True
            for quality in qualities:
                for edge in edges:
                    is_first = first_combo
                    first_combo = False
                    resized = _resize(normalized, edge)
                    try:
                        try:
                            data = _render(resized, suffix, quality, exif_bytes)
                        except Exception:
                            continue
                        size = len(data)
                        if best_size is None or size < best_size:
                            best_data = data
                            best_size = size
                        if config.target_bytes is not None:
                            if size <= config.target_bytes and (under_target_size is None or size > under_target_size):
                                under_target_data = data
                                under_target_size = size
                            if is_first and size <= config.target_bytes:
                                # Highest quality + largest edge already fits the target, so
                                # no other candidate can be a larger result under the target.
                                output.parent.mkdir(parents=True, exist_ok=True)
                                output.write_bytes(data)
                                return output
                        else:
                            # No target — first result (highest quality, largest edge) is best
                            output.parent.mkdir(parents=True, exist_ok=True)
                            output.write_bytes(data)
                            return output
                    finally:
                        if resized is not normalized:
                            resized.close()
        finally:
            if normalized is not raw:
                normalized.close()
    finally:
        if not raw_closed:
            raw.close()

    result = under_target_data or best_data
    if result is None:
        raise RuntimeError("Image compression produced no output")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    return output



def _render(image: Image.Image, suffix: str, quality: int, exif_bytes: Optional[bytes] = None) -> bytes:
    buffer = BytesIO()
    try:
        _save(image, buffer, suffix, quality, exif_bytes)
        return buffer.getvalue()
    finally:
        buffer.close()


def _resize(image: Image.Image, max_edge: Optional[int]) -> Image.Image:
    if max_edge is None or max_edge <= 0:
        return image
    w, h = image.size
    if w <= max_edge and h <= max_edge:
        return image
    ratio = min(max_edge / w, max_edge / h)
    new_size = (max(1, int(w * ratio)), max(1, int(h * ratio)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _normalize_mode(image: Image.Image, suffix: str) -> Image.Image:
    if suffix in {".jpg", ".jpeg"}:
        if "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info):
            base = Image.new("RGB", image.size, "white")
            try:
                rgba = image.convert("RGBA")
                alpha = None
                rgb = None
                try:
                    alpha = rgba.split()[-1]
                    rgb = image.convert("RGB")
                    base.paste(rgb, mask=alpha)
                finally:
                    if rgb is not None:
                        rgb.close()
                    if alpha is not None:
                        alpha.close()
                    rgba.close()
            except Exception:
                base.close()
                raise
            return base
        if image.mode not in {"RGB", "L"}:
            return image.convert("RGB")
    if suffix == ".webp" and image.mode not in {"RGB", "RGBA"}:
        if image.mode == "P" and "transparency" in image.info:
            return image.convert("RGBA")
        return image.convert("RGBA" if "A" in image.getbands() else "RGB")
    return image


def _save(image: Image.Image, buffer: BytesIO, suffix: str, quality: int, exif_bytes: Optional[bytes] = None) -> None:
    quality = clamp_quality(quality)
    if suffix in {".jpg", ".jpeg"}:
        kwargs: dict = {"quality": quality, "optimize": True, "progressive": True}
        if exif_bytes:
            kwargs["exif"] = exif_bytes
        image.save(buffer, format="JPEG", **kwargs)
    elif suffix == ".webp":
        image.save(buffer, format="WEBP", quality=quality, method=6)
    elif suffix == ".png":
        if quality < 95 and image.mode in {"RGB", "RGBA", "L"}:
            if image.mode == "RGBA":
                colors = max(16, min(256, int(quality / 95 * 256)))
                r, g, b, a = image.split()
                try:
                    rgb = Image.merge("RGB", (r, g, b))
                except Exception:
                    r.close()
                    g.close()
                    b.close()
                    a.close()
                    raise
                r.close()
                g.close()
                b.close()
                quantized = None
                result = None
                try:
                    quantized = rgb.quantize(colors=colors)
                    result = quantized.convert("RGBA")
                    result.putalpha(a)
                    result.save(buffer, format="PNG", optimize=True, compress_level=9)
                finally:
                    if result is not None:
                        result.close()
                    if quantized is not None:
                        quantized.close()
                    a.close()
                    rgb.close()
            else:
                colors = max(16, min(256, int(quality / 95 * 256)))
                quantized = image.quantize(colors=colors)
                try:
                    quantized.save(buffer, format="PNG", optimize=True, compress_level=9)
                finally:
                    quantized.close()
        else:
            image.save(buffer, format="PNG", optimize=True, compress_level=9)
    else:
        raise ValueError(f"Unsupported image format: {suffix}")


def _quality_candidates(start: int) -> list[int]:
    start = clamp_quality(start)
    if start <= 1:
        return [start]
    values = sorted(set(list(range(start, 0, -5)) + [1]), reverse=True)
    return values


def _edge_candidates(max_edge: Optional[int]) -> list[Optional[int]]:
    if max_edge is not None and max_edge > 0:
        values = [max_edge, int(max_edge * 0.9), int(max_edge * 0.8), int(max_edge * 0.7), int(max_edge * 0.6)]
        floor = max(64, max_edge // 4)
        clamped = [max(floor, v) for v in values]
        return list(dict.fromkeys(clamped))
    return [None, 2400, 2000, 1800, 1600, 1400, 1200, 1000, 800, 640]
