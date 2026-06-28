from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
SUPPORTED_PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_PDF_EXTENSIONS


@dataclass(frozen=True)
class CompressionConfig:
    quality: int = 82
    max_edge: Optional[int] = None
    to_webp: bool = False
    target_bytes: Optional[int] = None
    output_dir: Path = Path("compressed")
    overwrite: bool = False
    archive: Optional[str] = None
    pdf_mode: str = "auto"
    pdf_dpi: int = 120
    pdf_grayscale: bool = False
    strip_metadata: bool = True
    compression_level: int = 2  # 1=minimal, 2=balanced, 3=aggressive, 4=maximum

    def with_quality(self, quality: int) -> "CompressionConfig":
        return replace(self, quality=max(1, min(95, quality)))

    def with_target(self, target_bytes: Optional[int]) -> "CompressionConfig":
        return replace(self, target_bytes=target_bytes)


@dataclass(frozen=True)
class CompressionResult:
    source: Path
    output: Optional[Path]
    original_size: int
    compressed_size: Optional[int]
    status: str
    error: Optional[str] = None

    @property
    def saved_bytes(self) -> Optional[int]:
        if self.compressed_size is None:
            return None
        return self.original_size - self.compressed_size

    @property
    def compression_ratio(self) -> Optional[float]:
        if self.compressed_size is None or self.original_size == 0:
            return None
        return 1.0 - (self.compressed_size / self.original_size)


@dataclass
class CompressionSummary:
    results: list[CompressionResult] = field(default_factory=list)
    archive: Optional[CompressionResult] = None

    @property
    def all_results(self) -> list[CompressionResult]:
        values = list(self.results)
        if self.archive is not None:
            values.append(self.archive)
        return values
