from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from .models import SUPPORTED_EXTENSIONS, SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_PDF_EXTENSIONS

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?b?)?\s*$", re.IGNORECASE)


def parse_size(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    match = _SIZE_RE.match(value)
    if not match:
        raise ValueError(f"Invalid size: {value}")
    number = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    multipliers = {
        "": 1,
        "b": 1,
        "k": 1000,
        "kb": 1000,
        "m": 1000**2,
        "mb": 1000**2,
        "g": 1000**3,
        "gb": 1000**3,
        "t": 1000**4,
        "tb": 1000**4,
    }
    result = int(number * multipliers[unit])
    return result if result > 0 else None


def format_size(size: Optional[int]) -> str:
    if size is None:
        return "-"
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value >= 1000 and unit != "TB":
            value /= 1000
            continue
        return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
    return f"{value:.1f} PB"


def is_image(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_PDF_EXTENSIONS


def is_supported(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def iter_supported_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        if is_supported(path):
            yield path
        return
    for item in sorted(path.rglob("*")):
        if is_supported(item):
            yield item


def unique_path(path: Path, overwrite: bool) -> Path:
    if overwrite or not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for index in range(1, 10000):
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find free output path for {path}")


def clamp_quality(quality: int) -> int:
    return max(1, min(95, quality))


def relative_output_path(source: Path, root: Path, output_dir: Path, suffix: Optional[str], overwrite: bool) -> Path:
    rel = source.relative_to(root) if source != root else source.name
    target = output_dir / rel
    if suffix is not None:
        target = target.with_suffix(suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    return unique_path(target, overwrite)
