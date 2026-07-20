# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy>=2.2.6",
#     "PyMuPDF>=1.24.0",
#     "scikit-image>=0.25.2",
# ]
# ///

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Optional, Sequence

import fitz
import numpy as np
import skimage
from skimage.metrics import structural_similarity

_SCHEMA_VERSION = 2
_PAGE_SIZE_TOLERANCE_POINTS = 0.01
_SSIM_WIN_SIZE = 11
_LINK_FIELDS = ("kind", "from", "page", "to", "uri", "file", "nameddest", "zoom")


class BenchmarkError(RuntimeError):
    exit_code = 1

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class ComparisonError(BenchmarkError):
    exit_code = 3


def _open_pdf(stack: ExitStack, path: Path, role: str) -> fitz.Document:
    if not path.is_file():
        raise BenchmarkError("FILE_NOT_FOUND", f"{role} file does not exist", role=role, path=str(path))
    try:
        document = stack.enter_context(fitz.open(path))
    except Exception as exc:
        raise BenchmarkError(
            "PDF_OPEN_FAILED",
            f"Could not open {role} PDF: {exc}",
            role=role,
            path=str(path),
        ) from exc
    if not document.is_pdf:
        raise BenchmarkError("NOT_A_PDF", f"{role} is not a PDF", role=role, path=str(path))
    if document.needs_pass:
        raise BenchmarkError(
            "PASSWORD_REQUIRED",
            f"{role} PDF requires a password",
            role=role,
            path=str(path),
        )
    if document.page_count == 0:
        raise BenchmarkError("EMPTY_PDF", f"{role} PDF has no pages", role=role, path=str(path))
    return document


def _normalize_link_value(value: Any) -> Any:
    if isinstance(value, (fitz.Point, fitz.Rect)):
        return [_normalize_link_value(float(component)) for component in value]
    if isinstance(value, fitz.Quad):
        return [_normalize_link_value(point) for point in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BenchmarkError(
                "INVALID_LINK_VALUE",
                "Link metadata contains a non-finite number",
                value=repr(value),
            )
        return round(value, 6)
    if isinstance(value, (list, tuple)):
        return [_normalize_link_value(item) for item in value]
    return value


def _canonical_links(page: fitz.Page) -> list[dict[str, Any]]:
    links = [
        {
            field: _normalize_link_value(link[field])
            for field in _LINK_FIELDS
            if field in link
        }
        for link in page.get_links()
    ]
    return sorted(
        links,
        key=lambda link: json.dumps(
            link,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def _soft_mask_image_count(page: fitz.Page) -> int:
    return sum(1 for image in page.get_images(full=True) if image[1] > 0)


def _update_hash(digest: Any, payload: bytes) -> None:
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _document_stats(document: fitz.Document, path: Path) -> dict[str, Any]:
    text_chars = 0
    links = 0
    soft_mask_images = 0
    text_digest = hashlib.sha256()
    links_digest = hashlib.sha256()
    soft_mask_digest = hashlib.sha256()
    for page in document:
        text = page.get_text("text")
        page_links = _canonical_links(page)
        page_soft_mask_images = _soft_mask_image_count(page)
        text_chars += len(text)
        links += len(page_links)
        soft_mask_images += page_soft_mask_images
        _update_hash(text_digest, text.encode("utf-8"))
        _update_hash(
            links_digest,
            json.dumps(
                page_links,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8"),
        )
        _update_hash(
            soft_mask_digest,
            page_soft_mask_images.to_bytes(8, "big"),
        )
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "page_count": document.page_count,
        "text_chars": text_chars,
        "text_sha256": text_digest.hexdigest(),
        "links": links,
        "links_sha256": links_digest.hexdigest(),
        "soft_mask_images": soft_mask_images,
        "soft_mask_images_sha256": soft_mask_digest.hexdigest(),
    }


def _validate_geometry(
    original: fitz.Document,
    target: fitz.Document,
    role: str,
) -> None:
    if target.page_count != original.page_count:
        raise ComparisonError(
            "PAGE_COUNT_MISMATCH",
            f"{role} has {target.page_count} pages; expected {original.page_count}",
            role=role,
            expected=original.page_count,
            actual=target.page_count,
        )
    for index in range(original.page_count):
        expected = original[index].rect
        actual = target[index].rect
        if (
            abs(expected.width - actual.width) > _PAGE_SIZE_TOLERANCE_POINTS
            or abs(expected.height - actual.height) > _PAGE_SIZE_TOLERANCE_POINTS
        ):
            raise ComparisonError(
                "PAGE_SIZE_MISMATCH",
                f"{role} page {index + 1} has different dimensions",
                role=role,
                page=index + 1,
                expected=[expected.width, expected.height],
                actual=[actual.width, actual.height],
            )


def _render_rgb(page: fitz.Page, dpi: int) -> np.ndarray:
    scale = dpi / 72
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csRGB,
        alpha=False,
        annots=True,
    )
    return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height,
        pixmap.width,
        3,
    )


def _compare_documents(
    original: fitz.Document,
    target: fitz.Document,
    role: str,
    dpi: int,
    original_size: int,
    target_size: int,
) -> dict[str, Any]:
    _validate_geometry(original, target, role)
    pages = []
    for index in range(original.page_count):
        expected = _render_rgb(original[index], dpi)
        actual = _render_rgb(target[index], dpi)
        if expected.shape != actual.shape:
            raise ComparisonError(
                "RENDER_SHAPE_MISMATCH",
                f"{role} page {index + 1} rendered to a different shape",
                role=role,
                page=index + 1,
                expected=list(expected.shape),
                actual=list(actual.shape),
            )
        if min(expected.shape[:2]) < _SSIM_WIN_SIZE:
            raise ComparisonError(
                "PAGE_TOO_SMALL_FOR_SSIM",
                f"Page {index + 1} is too small for the SSIM window",
                role=role,
                page=index + 1,
                shape=list(expected.shape),
            )
        score = float(
            structural_similarity(
                expected,
                actual,
                data_range=255,
                win_size=_SSIM_WIN_SIZE,
                gaussian_weights=True,
                sigma=1.5,
                use_sample_covariance=False,
                channel_axis=2,
            )
        )
        if not math.isfinite(score):
            raise BenchmarkError(
                "INVALID_SSIM",
                f"{role} page {index + 1} produced a non-finite SSIM value",
                role=role,
                page=index + 1,
            )
        pages.append({"page": index + 1, "ssim": score})

    scores = [item["ssim"] for item in pages]
    minimum = min(scores)
    return {
        "baseline": "original",
        "pages": pages,
        "ssim_mean": sum(scores) / len(scores),
        "ssim_min": minimum,
        "ssim_min_page": scores.index(minimum) + 1,
        "size_ratio": target_size / original_size,
        "reduction_percent": (1 - target_size / original_size) * 100,
    }


def _append_preservation_warnings(
    warnings: list[dict[str, Any]],
    original: dict[str, Any],
    target: dict[str, Any],
    role: str,
) -> None:
    if target["text_sha256"] != original["text_sha256"]:
        warnings.append(
            {
                "code": "TEXT_CHANGED",
                "role": role,
                "expected": {
                    "chars": original["text_chars"],
                    "sha256": original["text_sha256"],
                },
                "actual": {
                    "chars": target["text_chars"],
                    "sha256": target["text_sha256"],
                },
            }
        )
    if target["links_sha256"] != original["links_sha256"]:
        warnings.append(
            {
                "code": "LINKS_CHANGED",
                "role": role,
                "expected": {
                    "count": original["links"],
                    "sha256": original["links_sha256"],
                },
                "actual": {
                    "count": target["links"],
                    "sha256": target["links_sha256"],
                },
            }
        )
    if target["soft_mask_images_sha256"] != original["soft_mask_images_sha256"]:
        warnings.append(
            {
                "code": "TRANSPARENCY_CHANGED",
                "role": role,
                "expected": {
                    "count": original["soft_mask_images"],
                    "sha256": original["soft_mask_images_sha256"],
                },
                "actual": {
                    "count": target["soft_mask_images"],
                    "sha256": target["soft_mask_images_sha256"],
                },
            }
        )


def compare_pdfs(
    original_path: Path,
    candidate_path: Path,
    *,
    reference_path: Optional[Path] = None,
    dpi: int = 96,
) -> dict[str, Any]:
    if not 36 <= dpi <= 300:
        raise BenchmarkError("INVALID_DPI", "DPI must be between 36 and 300", dpi=dpi)

    with ExitStack() as stack:
        original = _open_pdf(stack, original_path, "original")
        candidate = _open_pdf(stack, candidate_path, "candidate")
        reference = (
            _open_pdf(stack, reference_path, "reference")
            if reference_path is not None
            else None
        )

        documents = {
            "original": _document_stats(original, original_path),
            "candidate": _document_stats(candidate, candidate_path),
            "reference": (
                _document_stats(reference, reference_path)
                if reference is not None and reference_path is not None
                else None
            ),
        }
        warnings: list[dict[str, Any]] = []
        _append_preservation_warnings(
            warnings,
            documents["original"],
            documents["candidate"],
            "candidate",
        )
        comparisons = {
            "candidate": _compare_documents(
                original,
                candidate,
                "candidate",
                dpi,
                documents["original"]["size_bytes"],
                documents["candidate"]["size_bytes"],
            ),
            "reference": None,
        }
        if reference is not None and documents["reference"] is not None:
            _append_preservation_warnings(
                warnings,
                documents["original"],
                documents["reference"],
                "reference",
            )
            comparisons["reference"] = _compare_documents(
                original,
                reference,
                "reference",
                dpi,
                documents["original"]["size_bytes"],
                documents["reference"]["size_bytes"],
            )

    return {
        "schema_version": _SCHEMA_VERSION,
        "status": "ok",
        "settings": {
            "dpi": dpi,
            "render": {"colorspace": "rgb", "alpha": False, "annotations": True},
            "ssim": {
                "win_size": _SSIM_WIN_SIZE,
                "data_range": 255,
                "gaussian_weights": True,
                "sigma": 1.5,
                "use_sample_covariance": False,
                "channel_axis": 2,
            },
            "libraries": {
                "pymupdf": fitz.__version__,
                "numpy": np.__version__,
                "scikit_image": skimage.__version__,
            },
        },
        "documents": documents,
        "comparisons": comparisons,
        "warnings": warnings,
    }


def _print_human(report: dict[str, Any]) -> None:
    original = report["documents"]["original"]
    print(
        f"original: {original['size_bytes']:,} bytes, "
        f"{original['page_count']} pages, {original['text_chars']:,} text chars, "
        f"{original['links']} links"
    )
    for role in ("candidate", "reference"):
        document = report["documents"][role]
        comparison = report["comparisons"][role]
        if document is None or comparison is None:
            continue
        print(
            f"{role}: {document['size_bytes']:,} bytes, "
            f"SSIM mean={comparison['ssim_mean']:.6f}, "
            f"min={comparison['ssim_min']:.6f} "
            f"(page {comparison['ssim_min_page']}), "
            f"reduction={comparison['reduction_percent']:.2f}%"
        )
    for warning in report["warnings"]:
        count_key = "chars" if warning["code"] == "TEXT_CHANGED" else "count"
        print(
            f"warning: {warning['role']} {warning['code']} "
            f"({warning['expected'][count_key]} -> "
            f"{warning['actual'][count_key]}; semantic fingerprint differs)"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare PDF size, preservation, and page-level SSIM.",
        epilog=(
            "Exit codes: 0=success, 1=file/configuration error, "
            "2=invalid CLI arguments, 3=PDF geometry mismatch."
        ),
    )
    parser.add_argument("original", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--dpi", type=int, default=96)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = compare_pdfs(
            args.original,
            args.candidate,
            reference_path=args.reference,
            dpi=args.dpi,
        )
    except BenchmarkError as exc:
        error = {
            "schema_version": _SCHEMA_VERSION,
            "status": "error",
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": exc.details,
            },
        }
        if args.json_output:
            print(
                json.dumps(
                    error,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
            )
        else:
            print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        if args.json_output:
            print(
                json.dumps(
                    {
                        "schema_version": _SCHEMA_VERSION,
                        "status": "error",
                        "error": {"code": "BENCHMARK_FAILED", "message": str(exc)},
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
            )
        else:
            print(f"error [BENCHMARK_FAILED]: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
