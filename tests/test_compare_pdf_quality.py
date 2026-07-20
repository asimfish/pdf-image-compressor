import json
from pathlib import Path

import fitz
import pytest

from scripts import compare_pdf_quality


def _make_pdf(path: Path, page_texts: list[str], *, width: float = 595) -> Path:
    with fitz.open() as document:
        for text in page_texts:
            page = document.new_page(width=width, height=842)
            page.insert_text((72, 72), text, fontsize=20)
        document.save(path)
    return path


def _make_solid_color_pdf(path: Path, color: tuple[float, float, float]) -> Path:
    with fitz.open() as document:
        page = document.new_page(width=100, height=100)
        page.draw_rect(page.rect, color=color, fill=color)
        document.save(path)
    return path


def _make_pdf_with_link(path: Path, uri: str) -> Path:
    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), "link", fontsize=20)
        page.insert_link(
            {
                "kind": fitz.LINK_URI,
                "from": fitz.Rect(72, 80, 140, 100),
                "uri": uri,
            }
        )
        document.save(path)
    return path


def _make_pdf_with_transparent_image(path: Path, *, preserve_alpha: bool) -> Path:
    import io

    from PIL import Image, ImageDraw

    with Image.new("RGBA", (120, 80), (0, 0, 0, 0)) as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 20, 90, 60), fill=(220, 30, 30, 255))
        with io.BytesIO() as buffer:
            if preserve_alpha:
                image.save(buffer, format="PNG")
            else:
                with Image.new("RGB", image.size, "white") as background:
                    with image.getchannel("A") as alpha:
                        background.paste(image, mask=alpha)
                    background.save(buffer, format="JPEG", quality=95)
            data = buffer.getvalue()

    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_image(fitz.Rect(72, 100, 312, 260), stream=data)
        document.save(path)
    return path


def test_compare_identical_pdf_has_perfect_ssim(tmp_path: Path):
    original = _make_pdf(tmp_path / "original.pdf", ["first", "second"])

    report = compare_pdf_quality.compare_pdfs(original, original, dpi=72)

    comparison = report["comparisons"]["candidate"]
    assert comparison["ssim_mean"] == pytest.approx(1.0)
    assert comparison["ssim_min"] == pytest.approx(1.0)
    assert comparison["ssim_min_page"] == 1
    assert report["warnings"] == []


def test_compare_pdf_ssim_is_color_sensitive(tmp_path: Path):
    original = _make_solid_color_pdf(tmp_path / "red.pdf", (1, 0, 0))
    candidate = _make_solid_color_pdf(tmp_path / "green.pdf", (0, 0.51, 0))

    report = compare_pdf_quality.compare_pdfs(original, candidate, dpi=72)

    comparison = report["comparisons"]["candidate"]
    assert report["settings"]["render"]["colorspace"] == "rgb"
    assert report["settings"]["ssim"]["channel_axis"] == 2
    assert comparison["ssim_mean"] < 0.9


def test_compare_pdf_identifies_changed_page(tmp_path: Path):
    original = _make_pdf(tmp_path / "original.pdf", ["first", "second"])
    candidate = _make_pdf(tmp_path / "candidate.pdf", ["first", "switch"])

    report = compare_pdf_quality.compare_pdfs(original, candidate, dpi=72)

    pages = report["comparisons"]["candidate"]["pages"]
    assert pages[0]["ssim"] == pytest.approx(1.0)
    assert pages[1]["ssim"] < 1.0
    assert report["comparisons"]["candidate"]["ssim_min_page"] == 2
    assert any(item["code"] == "TEXT_CHANGED" for item in report["warnings"])


def test_compare_pdf_detects_changed_link_target(tmp_path: Path):
    original = _make_pdf_with_link(tmp_path / "original.pdf", "https://example.com/a")
    candidate = _make_pdf_with_link(tmp_path / "candidate.pdf", "https://example.com/b")

    report = compare_pdf_quality.compare_pdfs(original, candidate, dpi=72)

    assert report["documents"]["original"]["links"] == 1
    assert report["documents"]["candidate"]["links"] == 1
    assert report["comparisons"]["candidate"]["ssim_mean"] == pytest.approx(1.0)
    assert any(item["code"] == "LINKS_CHANGED" for item in report["warnings"])


def test_compare_pdf_detects_removed_soft_mask(tmp_path: Path):
    original = _make_pdf_with_transparent_image(
        tmp_path / "transparent.pdf",
        preserve_alpha=True,
    )
    candidate = _make_pdf_with_transparent_image(
        tmp_path / "flattened.pdf",
        preserve_alpha=False,
    )

    report = compare_pdf_quality.compare_pdfs(original, candidate, dpi=72)

    assert report["documents"]["original"]["soft_mask_images"] == 1
    assert report["documents"]["candidate"]["soft_mask_images"] == 0
    assert any(item["code"] == "TRANSPARENCY_CHANGED" for item in report["warnings"])


def test_compare_pdf_includes_reference_comparison(tmp_path: Path):
    original = _make_pdf(tmp_path / "original.pdf", ["same"])
    candidate = _make_pdf(tmp_path / "candidate.pdf", ["same"])
    reference = _make_pdf(tmp_path / "reference.pdf", ["same"])

    report = compare_pdf_quality.compare_pdfs(
        original,
        candidate,
        reference_path=reference,
        dpi=72,
    )

    assert report["comparisons"]["reference"]["ssim_mean"] == pytest.approx(1.0)
    assert report["documents"]["reference"]["text_sha256"]


def test_compare_pdf_rejects_page_count_mismatch(tmp_path: Path):
    original = _make_pdf(tmp_path / "original.pdf", ["first", "second"])
    candidate = _make_pdf(tmp_path / "candidate.pdf", ["first"])

    with pytest.raises(compare_pdf_quality.ComparisonError) as exc_info:
        compare_pdf_quality.compare_pdfs(original, candidate, dpi=72)

    assert exc_info.value.code == "PAGE_COUNT_MISMATCH"


def test_compare_pdf_rejects_page_size_mismatch(tmp_path: Path):
    original = _make_pdf(tmp_path / "original.pdf", ["same"])
    candidate = _make_pdf(tmp_path / "candidate.pdf", ["same"], width=594)

    with pytest.raises(compare_pdf_quality.ComparisonError) as exc_info:
        compare_pdf_quality.compare_pdfs(original, candidate, dpi=72)

    assert exc_info.value.code == "PAGE_SIZE_MISMATCH"


@pytest.mark.parametrize("dpi", [35, 301])
def test_compare_pdf_rejects_invalid_dpi(tmp_path: Path, dpi: int):
    with pytest.raises(compare_pdf_quality.BenchmarkError) as exc_info:
        compare_pdf_quality.compare_pdfs(
            tmp_path / "original.pdf",
            tmp_path / "candidate.pdf",
            dpi=dpi,
        )

    assert exc_info.value.code == "INVALID_DPI"


def test_compare_pdf_json_cli_output(tmp_path: Path, capsys):
    original = _make_pdf(tmp_path / "original.pdf", ["same"])

    exit_code = compare_pdf_quality.main(
        [str(original), str(original), "--dpi", "72", "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["comparisons"]["candidate"]["ssim_mean"] == pytest.approx(1.0)


def test_compare_pdf_json_cli_reports_missing_file(tmp_path: Path, capsys):
    missing = tmp_path / "missing.pdf"

    exit_code = compare_pdf_quality.main([str(missing), str(missing), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "FILE_NOT_FOUND"


def test_compare_pdf_human_cli_output(tmp_path: Path, capsys):
    original = _make_pdf(tmp_path / "original.pdf", ["same"])

    exit_code = compare_pdf_quality.main([str(original), str(original), "--dpi", "72"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "candidate:" in output
    assert "SSIM mean=1.000000" in output


def test_normalize_link_rejects_non_finite_number():
    with pytest.raises(compare_pdf_quality.BenchmarkError) as exc_info:
        compare_pdf_quality._normalize_link_value(float("inf"))

    assert exc_info.value.code == "INVALID_LINK_VALUE"
