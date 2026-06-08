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

    try:
        raw = ImageOps.exif_transpose(Image.open(source))
    except UnidentifiedImageError as exc:
        raise RuntimeError(f"Unsupported or corrupt image: {source}") from exc
    exif_bytes = raw.info.get("exif") if not config.strip_metadata else None

    normalized = _normalize_mode(raw, suffix)
    if config.target_bytes and None in edges:
        pixels = normalized.width * normalized.height
        if pixels > config.target_bytes * 100:
            edges = [e for e in edges if e is not None]
    for edge in edges:
        resized = _resize(normalized, edge)
        for quality in qualities:
            data = _render(resized, suffix, quality, exif_bytes)
            size = len(data)
            if best_size is None or size < best_size:
                best_data = data
                best_size = size
            if config.target_bytes is None or size <= config.target_bytes:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
                return output

    if best_data is None:
        raise RuntimeError("Image compression produced no output")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(best_data)
    return output



def _render(image: Image.Image, suffix: str, quality: int, exif_bytes: Optional[bytes] = None) -> bytes:
    buffer = BytesIO()
    _save(image, buffer, suffix, quality, exif_bytes)
    return buffer.getvalue()


def _resize(image: Image.Image, max_edge: Optional[int]) -> Image.Image:
    if max_edge is None or max_edge <= 0:
        return image
    copy = image.copy()
    copy.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return copy


def _normalize_mode(image: Image.Image, suffix: str) -> Image.Image:
    if suffix in {".jpg", ".jpeg"}:
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            base = Image.new("RGB", image.size, "white")
            alpha = image.convert("RGBA").split()[-1]
            base.paste(image.convert("RGB"), mask=alpha)
            return base
        if image.mode != "RGB":
            return image.convert("RGB")
    if suffix == ".webp" and image.mode not in {"RGB", "RGBA"}:
        return image.convert("RGBA" if "A" in image.getbands() else "RGB")
    return image.copy()


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
        if quality < 95 and image.mode in {"RGB", "RGBA"}:
            colors = max(16, min(256, int(quality / 95 * 256)))
            image = image.quantize(colors=colors)
        image.save(buffer, format="PNG", optimize=True, compress_level=9)
    else:
        raise ValueError(f"Unsupported image format: {suffix}")


def _quality_candidates(start: int) -> list[int]:
    start = clamp_quality(start)
    if start <= 15:
        return [start]
    values = list(range(start, 14, -5))
    if values[-1] != 15:
        values.append(15)
    return values


def _edge_candidates(max_edge: Optional[int]) -> list[Optional[int]]:
    if max_edge is not None and max_edge > 0:
        values = [max_edge, int(max_edge * 0.9), int(max_edge * 0.8), int(max_edge * 0.7), int(max_edge * 0.6)]
        clamped = [max(320, value) for value in values]
        return list(dict.fromkeys(clamped))
    return [None, 2400, 2000, 1800, 1600, 1400, 1200, 1000, 800, 640]
