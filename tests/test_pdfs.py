from pathlib import Path

import fitz
import pytest

from file_compressor.models import CompressionConfig
from file_compressor.pdfs import compress_pdf, optimize_images_in_pdf, optimize_pdf, rasterize_pdf, render_page


def _make_pdf(path: Path, pages: int = 3, with_images: bool = False) -> Path:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1}: Hello World", fontsize=24)
        if with_images:
            # Draw a colored rectangle as "image" content
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(100, 100, 300, 300))
            shape.finish(color=(1, 0, 0), fill=(1, 0, 0))
            shape.commit()
    doc.save(path)
    doc.close()
    return path


def test_optimize_pdf_reduces_size(tmp_path: Path):
    source = _make_pdf(tmp_path / "input.pdf", pages=5)
    output = tmp_path / "optimized.pdf"
    original_size = source.stat().st_size

    config = CompressionConfig(output_dir=tmp_path)
    result = optimize_pdf(source, output, config)

    assert result.exists()
    assert output.stat().st_size <= original_size


def test_optimize_pdf_strips_metadata(tmp_path: Path):
    source = _make_pdf(tmp_path / "meta.pdf")
    output = tmp_path / "stripped.pdf"

    config = CompressionConfig(strip_metadata=True, output_dir=tmp_path)
    optimize_pdf(source, output, config)

    doc = fitz.open(output)
    meta = doc.metadata
    doc.close()
    assert meta.get("title", "") == "" or meta.get("title") is None


def test_rasterize_pdf_basic(tmp_path: Path):
    source = _make_pdf(tmp_path / "raster_in.pdf", pages=2)
    output = tmp_path / "raster_out.pdf"

    rasterize_pdf(source, output, dpi=100, quality=70, grayscale=False, strip_metadata=True)

    assert output.exists()
    doc = fitz.open(output)
    assert len(doc) == 2
    doc.close()


def test_rasterize_pdf_grayscale(tmp_path: Path):
    source = _make_pdf(tmp_path / "gray_in.pdf")
    output = tmp_path / "gray_out.pdf"

    rasterize_pdf(source, output, dpi=100, quality=60, grayscale=True, strip_metadata=True)

    assert output.exists()
    assert output.stat().st_size > 0


def test_rasterize_pdf_keep_metadata(tmp_path: Path):
    source = _make_pdf(tmp_path / "meta_in.pdf")
    output = tmp_path / "meta_out.pdf"

    rasterize_pdf(source, output, dpi=100, quality=70, grayscale=False, strip_metadata=False)

    assert output.exists()
    assert output.stat().st_size > 0


def test_compress_pdf_auto_mode(tmp_path: Path):
    source = _make_pdf(tmp_path / "auto.pdf", pages=3)
    output = tmp_path / "auto_out.pdf"

    config = CompressionConfig(pdf_mode="auto", output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()


def test_compress_pdf_optimize_mode(tmp_path: Path):
    source = _make_pdf(tmp_path / "opt.pdf")
    output = tmp_path / "opt_out.pdf"

    config = CompressionConfig(pdf_mode="optimize", output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()


def test_compress_pdf_raster_mode(tmp_path: Path):
    source = _make_pdf(tmp_path / "rast.pdf")
    output = tmp_path / "rast_out.pdf"

    config = CompressionConfig(pdf_mode="raster", pdf_dpi=100, quality=60, output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()


def test_compress_pdf_invalid_mode(tmp_path: Path):
    source = _make_pdf(tmp_path / "bad.pdf")
    output = tmp_path / "bad_out.pdf"

    config = CompressionConfig(pdf_mode="invalid", output_dir=tmp_path)
    with pytest.raises(ValueError, match="pdf_mode"):
        compress_pdf(source, output, config)


def _make_pdf_with_image(path: Path, pages: int = 2) -> Path:
    """Build a PDF with selectable text AND a real embedded raster image."""
    import io
    import random
    from PIL import Image

    with Image.new("RGB", (1000, 800)) as img:
        px = img.load()
        random.seed(1)
        for y in range(0, 800, 4):
            for x in range(0, 1000, 4):
                c = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                for dy in range(4):
                    for dx in range(4):
                        if x + dx < 1000 and y + dy < 800:
                            px[x + dx, y + dy] = c
        with io.BytesIO() as buf:
            img.save(buf, format="JPEG", quality=95)
            data = buf.getvalue()

    with fitz.open() as doc:
        for i in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {i + 1}: searchable text content", fontsize=18)
            page.insert_image(fitz.Rect(50, 100, 550, 500), stream=data)
        doc.save(path)
    return path


def _text_chars(path: Path) -> int:
    with fitz.open(path) as doc:
        return sum(len(p.get_text()) for p in doc)


def _make_pdf_with_soft_mask(path: Path, *, matte: bool = False) -> Path:
    """Build a PDF image with separate base-color and soft-mask objects."""
    import io
    import random

    from PIL import Image

    rng = random.Random(7)
    transparent = (255, 255, 255, 0) if matte else (0, 0, 0, 0)
    with Image.new("RGBA", (600, 400), transparent) as image:
        with Image.new("RGBA", (80, 80), (240, 30, 30, 128)) as partial:
            image.alpha_composite(partial, (40, 40))
        with Image.frombytes("RGB", (400, 240), rng.randbytes(400 * 240 * 3)) as rgb:
            with Image.new("L", rgb.size, 255) as alpha:
                with Image.new("RGBA", rgb.size) as visible:
                    visible.paste(rgb)
                    visible.putalpha(alpha)
                    image.alpha_composite(visible, (100, 80))
        with io.BytesIO() as buffer:
            image.save(buffer, format="PNG")
            data = buffer.getvalue()

    with fitz.open() as doc:
        page = doc.new_page()
        image_rect = fitz.Rect(50, 100, 550, 433.33)
        page.draw_rect(image_rect, color=(0.1, 0.4, 0.8), fill=(0.1, 0.4, 0.8))
        page.insert_image(image_rect, stream=data)
        if matte:
            smask_xref = page.get_images(full=True)[0][1]
            assert smask_xref > 0
            doc.xref_set_key(smask_xref, "Matte", "[1 1 1]")
        doc.save(path)
    return path


def _make_pdf_with_shared_soft_mask(path: Path) -> Path:
    """Build two distinct base images that reference one shared soft mask."""
    import io
    import random

    from PIL import Image

    streams: list[bytes] = []
    for index, seed in enumerate((11, 29)):
        rng = random.Random(seed)
        with Image.new("RGBA", (300, 200), (0, 0, 0, 0)) as image:
            patch_size = (220, 120 - index)
            with Image.frombytes("RGB", patch_size, rng.randbytes(patch_size[0] * patch_size[1] * 3)) as rgb:
                with Image.new("L", rgb.size, 255) as alpha:
                    with Image.new("RGBA", rgb.size) as visible:
                        visible.paste(rgb)
                        visible.putalpha(alpha)
                        image.alpha_composite(visible, (40, 40))
            with io.BytesIO() as buffer:
                image.save(buffer, format="PNG")
                streams.append(buffer.getvalue())

    with fitz.open() as doc:
        page = doc.new_page(width=700, height=500)
        page.draw_rect(page.rect, color=(0.1, 0.4, 0.8), fill=(0.1, 0.4, 0.8))
        page.insert_image(fitz.Rect(20, 40, 320, 240), stream=streams[0])
        page.insert_image(fitz.Rect(360, 260, 660, 460), stream=streams[1])
        images = page.get_images(full=True)
        smask_by_image_xref = {image[0]: image[1] for image in images}
        image_xrefs = sorted(
            smask_by_image_xref,
            key=lambda xref: page.get_image_rects(xref)[0].x0,
        )
        assert len(image_xrefs) == 2
        shared_smask_xref = smask_by_image_xref[image_xrefs[0]]
        assert shared_smask_xref > 0
        assert shared_smask_xref != smask_by_image_xref[image_xrefs[1]]
        doc.xref_set_key(image_xrefs[1], "SMask", f"{shared_smask_xref} 0 R")
        doc.save(path)
    return path


def _page_rgb_at(path: Path, x: int, y: int) -> tuple[int, int, int]:
    with fitz.open(path) as doc:
        pixmap = doc[0].get_pixmap(matrix=fitz.Matrix(1, 1), colorspace=fitz.csRGB, alpha=False)
        offset = (y * pixmap.width + x) * 3
        samples = pixmap.samples
        return samples[offset], samples[offset + 1], samples[offset + 2]


def _assert_transparent_pixel_preserved(source: Path, output: Path) -> None:
    expected = _page_rgb_at(source, 60, 110)
    actual = _page_rgb_at(output, 60, 110)
    assert max(abs(expected[index] - actual[index]) for index in range(3)) <= 2


def _assert_partial_transparency_preserved(source: Path, output: Path) -> None:
    # This page coordinate falls inside the synthetic red patch with alpha=128.
    expected = _page_rgb_at(source, 117, 167)
    actual = _page_rgb_at(output, 117, 167)
    assert max(abs(expected[index] - actual[index]) for index in range(3)) <= 10


def _assert_shared_mask_transparency_preserved(source: Path, output: Path) -> None:
    for x, y in ((30, 50), (370, 270)):
        expected = _page_rgb_at(source, x, y)
        actual = _page_rgb_at(output, x, y)
        assert max(abs(expected[index] - actual[index]) for index in range(3)) <= 2


def _assert_scaled_image_keeps_independent_soft_mask(path: Path) -> None:
    with fitz.open(path) as doc:
        xref, smask_xref = doc[0].get_images(full=True)[0][:2]
        assert smask_xref > 0
        image = doc.extract_image(xref)
        soft_mask = doc.extract_image(smask_xref)
        assert (image["width"], image["height"]) != (
            soft_mask["width"],
            soft_mask["height"],
        )


def _assert_matte_image_and_soft_mask_dimensions_match(path: Path) -> None:
    with fitz.open(path) as doc:
        xref, smask_xref = doc[0].get_images(full=True)[0][:2]
        assert doc.xref_get_key(smask_xref, "Matte")[0] == "array"
        image = doc.extract_image(xref)
        soft_mask = doc.extract_image(smask_xref)
        assert (image["width"], image["height"]) == (
            soft_mask["width"],
            soft_mask["height"],
        )


def test_default_pdf_mode_is_fidelity():
    assert CompressionConfig().pdf_mode == "fidelity"


def test_compress_pdf_fidelity_mode_preserves_text_and_never_grows(tmp_path: Path):
    source = _make_pdf_with_image(tmp_path / "fidelity.pdf")
    output = tmp_path / "fidelity_out.pdf"
    original_size = source.stat().st_size

    compress_pdf(
        source,
        output,
        CompressionConfig(pdf_mode="fidelity", output_dir=tmp_path),
    )

    assert output.exists()
    assert output.stat().st_size <= original_size
    assert _text_chars(output) == _text_chars(source)


def test_compress_pdf_text_mode_preserves_soft_mask_transparency(tmp_path: Path):
    source = _make_pdf_with_soft_mask(tmp_path / "soft_mask.pdf")
    output = tmp_path / "soft_mask_out.pdf"
    with fitz.open(source) as doc:
        assert doc[0].get_images(full=True)[0][1] > 0

    compress_pdf(
        source,
        output,
        CompressionConfig(pdf_mode="text", compression_level=2, output_dir=tmp_path),
    )

    _assert_transparent_pixel_preserved(source, output)
    _assert_partial_transparency_preserved(source, output)
    _assert_scaled_image_keeps_independent_soft_mask(output)


def test_text_mode_keeps_matte_soft_mask_dimensions_aligned(tmp_path: Path):
    source = _make_pdf_with_soft_mask(tmp_path / "soft_mask_matte.pdf", matte=True)
    output = tmp_path / "soft_mask_matte_out.pdf"

    compress_pdf(
        source,
        output,
        CompressionConfig(pdf_mode="text", compression_level=2, output_dir=tmp_path),
    )

    _assert_matte_image_and_soft_mask_dimensions_match(output)


def test_optimize_images_in_pdf_preserves_soft_mask_transparency(tmp_path: Path):
    source = _make_pdf_with_soft_mask(tmp_path / "soft_mask_optimize.pdf")
    output = tmp_path / "soft_mask_optimize_out.pdf"

    optimize_images_in_pdf(
        source,
        output,
        CompressionConfig(compression_level=2, output_dir=tmp_path),
    )

    _assert_transparent_pixel_preserved(source, output)
    _assert_partial_transparency_preserved(source, output)
    _assert_scaled_image_keeps_independent_soft_mask(output)


def test_optimize_images_preserves_shared_soft_mask(tmp_path: Path):
    source = _make_pdf_with_shared_soft_mask(tmp_path / "shared_soft_mask.pdf")
    output = tmp_path / "shared_soft_mask_out.pdf"

    optimize_images_in_pdf(
        source,
        output,
        CompressionConfig(compression_level=2, output_dir=tmp_path),
    )

    _assert_shared_mask_transparency_preserved(source, output)
    with fitz.open(output) as doc:
        images = doc[0].get_images(full=True)
        image_xrefs = list(dict.fromkeys(image[0] for image in images))
        smask_xrefs = {image[1] for image in images}
        assert len(image_xrefs) == 2
        assert len(smask_xrefs) == 1
        assert next(iter(smask_xrefs)) > 0
        assert all(doc.extract_image(xref)["width"] < 300 for xref in image_xrefs)


def test_optimize_images_keeps_matte_soft_mask_dimensions_aligned(tmp_path: Path):
    source = _make_pdf_with_soft_mask(
        tmp_path / "soft_mask_matte_optimize.pdf",
        matte=True,
    )
    output = tmp_path / "soft_mask_matte_optimize_out.pdf"

    optimize_images_in_pdf(
        source,
        output,
        CompressionConfig(compression_level=2, output_dir=tmp_path),
    )

    _assert_matte_image_and_soft_mask_dimensions_match(output)


def test_prepare_pdf_image_rejects_non_stream_soft_mask(caplog: pytest.LogCaptureFixture):
    import logging
    from contextlib import ExitStack

    from PIL import Image

    from file_compressor.pdfs import _prepare_pdf_image_for_jpeg

    class MissingMaskDocument:
        @staticmethod
        def is_stream(_xref: int) -> bool:
            return False

        @staticmethod
        def xref_get_key(_xref: int, _key: str) -> tuple[str, str]:
            raise AssertionError("non-stream soft masks have no subtype")

        @staticmethod
        def xref_object(_xref: int, *, compressed: bool) -> str:
            raise AssertionError("non-stream soft masks have no subtype object")

    with Image.new("RGB", (10, 10), "black") as image, ExitStack() as stack:
        with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match="soft mask 99"):
            _prepare_pdf_image_for_jpeg(
                image,
                stack,
                doc=MissingMaskDocument(),
                smask_xref=99,
            )

    assert "soft mask 99" in caplog.text


def test_prepare_pdf_image_validates_soft_mask_without_decoding():
    from contextlib import ExitStack

    from PIL import Image

    from file_compressor.pdfs import _prepare_pdf_image_for_jpeg

    class StreamMaskDocument:
        @staticmethod
        def is_stream(_xref: int) -> bool:
            return True

        @staticmethod
        def xref_get_key(_xref: int, key: str) -> tuple[str, str]:
            assert key == "Subtype"
            return "name", "/Image"

        @staticmethod
        def xref_object(_xref: int, *, compressed: bool) -> str:
            raise AssertionError("direct image subtypes have no indirect object")

    with Image.new("RGB", (10, 10), "black") as image, ExitStack() as stack:
        prepared = _prepare_pdf_image_for_jpeg(
            image,
            stack,
            doc=StreamMaskDocument(),
            smask_xref=99,
        )

    assert prepared is image


def test_prepare_pdf_image_rejects_non_image_stream_soft_mask(
    caplog: pytest.LogCaptureFixture,
):
    import logging
    from contextlib import ExitStack

    from PIL import Image

    from file_compressor.pdfs import _prepare_pdf_image_for_jpeg

    class NonImageStreamDocument:
        @staticmethod
        def is_stream(_xref: int) -> bool:
            return True

        @staticmethod
        def xref_get_key(_xref: int, key: str) -> tuple[str, str]:
            assert key == "Subtype"
            return "name", "/Form"

        @staticmethod
        def xref_object(_xref: int, *, compressed: bool) -> str:
            raise AssertionError("direct non-image subtypes have no indirect object")

    with Image.new("RGB", (10, 10), "black") as image, ExitStack() as stack:
        with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match="soft mask 99"):
            _prepare_pdf_image_for_jpeg(
                image,
                stack,
                doc=NonImageStreamDocument(),
                smask_xref=99,
            )

    assert "PDF image stream" in caplog.text


def test_prepare_pdf_image_accepts_indirect_image_subtype():
    from contextlib import ExitStack

    from PIL import Image

    from file_compressor.pdfs import _prepare_pdf_image_for_jpeg

    class IndirectImageSubtypeDocument:
        @staticmethod
        def is_stream(_xref: int) -> bool:
            return True

        @staticmethod
        def xref_get_key(_xref: int, key: str) -> tuple[str, str]:
            assert key == "Subtype"
            return "xref", "123 0 R"

        @staticmethod
        def xref_object(xref: int, *, compressed: bool) -> str:
            assert xref == 123
            assert compressed
            return "/Image"

    with Image.new("RGB", (10, 10), "black") as image, ExitStack() as stack:
        prepared = _prepare_pdf_image_for_jpeg(
            image,
            stack,
            doc=IndirectImageSubtypeDocument(),
            smask_xref=99,
        )

    assert prepared is image


def test_prepare_pdf_image_rejects_non_ascii_indirect_subtype():
    from contextlib import ExitStack

    from PIL import Image

    from file_compressor.pdfs import _prepare_pdf_image_for_jpeg

    class NonAsciiIndirectSubtypeDocument:
        @staticmethod
        def is_stream(_xref: int) -> bool:
            return True

        @staticmethod
        def xref_get_key(_xref: int, key: str) -> tuple[str, str]:
            assert key == "Subtype"
            return "xref", "１２３ ０ R"

        @staticmethod
        def xref_object(_xref: int, *, compressed: bool) -> str:
            assert compressed
            return "/Image"

    with Image.new("RGB", (10, 10), "black") as image, ExitStack() as stack:
        with pytest.raises(ValueError, match="soft mask 99"):
            _prepare_pdf_image_for_jpeg(
                image,
                stack,
                doc=NonAsciiIndirectSubtypeDocument(),
                smask_xref=99,
            )


def test_prepare_pdf_image_logs_malformed_indirect_subtype(
    caplog: pytest.LogCaptureFixture,
):
    import logging
    from contextlib import ExitStack

    from PIL import Image

    from file_compressor.pdfs import _prepare_pdf_image_for_jpeg

    class MalformedIndirectSubtypeDocument:
        @staticmethod
        def is_stream(_xref: int) -> bool:
            return True

        @staticmethod
        def xref_get_key(_xref: int, key: str) -> tuple[str, str]:
            assert key == "Subtype"
            return "xref", "123 invalid R"

        @staticmethod
        def xref_object(_xref: int, *, compressed: bool) -> str:
            raise AssertionError("malformed references must not be resolved")

    with Image.new("RGB", (10, 10), "black") as image, ExitStack() as stack:
        with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match="soft mask 99"):
            _prepare_pdf_image_for_jpeg(
                image,
                stack,
                doc=MalformedIndirectSubtypeDocument(),
                smask_xref=99,
            )

    assert "malformed indirect Subtype reference" in caplog.text


def test_prepare_pdf_image_logs_soft_mask_inspection_failure(
    caplog: pytest.LogCaptureFixture,
):
    import logging
    from contextlib import ExitStack

    from PIL import Image

    from file_compressor.pdfs import _prepare_pdf_image_for_jpeg

    class FailingMaskDocument:
        @staticmethod
        def is_stream(_xref: int) -> bool:
            raise RuntimeError("simulated xref inspection failure")

        @staticmethod
        def xref_get_key(_xref: int, _key: str) -> tuple[str, str]:
            raise AssertionError("stream inspection should fail first")

        @staticmethod
        def xref_object(_xref: int, *, compressed: bool) -> str:
            raise AssertionError("stream inspection should fail first")

    with Image.new("RGB", (10, 10), "black") as image, ExitStack() as stack:
        with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match="soft mask 99"):
            _prepare_pdf_image_for_jpeg(
                image,
                stack,
                doc=FailingMaskDocument(),
                smask_xref=99,
            )

    assert "Unable to inspect PDF soft mask 99" in caplog.text


def test_soft_mask_dimension_check_fails_closed(caplog: pytest.LogCaptureFixture):
    import logging

    from file_compressor.pdfs import _soft_mask_requires_matching_dimensions

    class FailingDocument:
        @staticmethod
        def xref_get_key(_xref: int, _key: str) -> tuple[str, str]:
            raise RuntimeError("simulated xref inspection failure")

    with caplog.at_level(logging.WARNING, logger="file_compressor.pdfs"):
        assert _soft_mask_requires_matching_dimensions(FailingDocument(), 99)

    assert "soft mask 99" in caplog.text
    assert "preserving image dimensions" in caplog.text


def test_replace_image_aborts_when_soft_mask_restore_fails(caplog: pytest.LogCaptureFixture):
    import logging

    from file_compressor.pdfs import (
        _SoftMaskRestoreError,
        _replace_pdf_image_preserving_soft_mask,
    )

    class RecordingPage:
        replaced_stream: bytes | None = None

        def replace_image(self, _xref: int, *, stream: bytes) -> None:
            self.replaced_stream = stream

    class FailingDocument:
        @staticmethod
        def xref_set_key(_xref: int, _key: str, _value: str) -> None:
            raise RuntimeError("simulated xref failure")

    page = RecordingPage()
    with caplog.at_level(logging.ERROR, logger="file_compressor.pdfs"):
        with pytest.raises(_SoftMaskRestoreError, match="soft mask 99"):
            _replace_pdf_image_preserving_soft_mask(
                page,
                FailingDocument(),
                xref=7,
                smask_xref=99,
                stream=b"jpeg",
            )

    assert page.replaced_stream == b"jpeg"
    assert "xref=7" in caplog.text
    assert "soft mask 99" in caplog.text


def test_text_mode_keeps_original_image_when_soft_mask_preparation_fails(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    import logging
    from unittest.mock import patch

    from file_compressor.pdfs import _prepare_pdf_image_for_jpeg

    source = _make_pdf_with_soft_mask(tmp_path / "soft_mask_broken.pdf")
    output = tmp_path / "soft_mask_broken_out.pdf"

    def reject_soft_mask(image, stack, *, doc, smask_xref):
        if smask_xref > 0:
            raise ValueError("broken soft mask")
        return _prepare_pdf_image_for_jpeg(image, stack, doc=doc, smask_xref=smask_xref)

    with caplog.at_level(logging.DEBUG, logger="file_compressor.pdfs"):
        with patch(
            "file_compressor.pdfs._prepare_pdf_image_for_jpeg",
            side_effect=reject_soft_mask,
        ):
            compress_pdf(
                source,
                output,
                CompressionConfig(pdf_mode="text", compression_level=2, output_dir=tmp_path),
            )

    _assert_transparent_pixel_preserved(source, output)
    with fitz.open(output) as doc:
        assert doc[0].get_images(full=True)[0][1] > 0
    assert "Skipping PDF image xref=" in caplog.text


def test_optimize_images_keeps_original_when_soft_mask_preparation_fails(tmp_path: Path):
    from unittest.mock import patch

    from file_compressor.pdfs import _prepare_pdf_image_for_jpeg

    source = _make_pdf_with_soft_mask(tmp_path / "soft_mask_optimize_broken.pdf")
    output = tmp_path / "soft_mask_optimize_broken_out.pdf"

    def reject_soft_mask(image, stack, *, doc, smask_xref):
        if smask_xref > 0:
            raise ValueError("broken soft mask")
        return _prepare_pdf_image_for_jpeg(image, stack, doc=doc, smask_xref=smask_xref)

    with patch(
        "file_compressor.pdfs._prepare_pdf_image_for_jpeg",
        side_effect=reject_soft_mask,
    ):
        optimize_images_in_pdf(
            source,
            output,
            CompressionConfig(compression_level=2, output_dir=tmp_path),
        )

    _assert_transparent_pixel_preserved(source, output)
    with fitz.open(output) as doc:
        assert doc[0].get_images(full=True)[0][1] > 0


def test_text_mode_does_not_save_partial_soft_mask_replacement(tmp_path: Path):
    from unittest.mock import patch

    from file_compressor.pdfs import _SoftMaskRestoreError

    source = _make_pdf_with_soft_mask(tmp_path / "soft_mask_restore_failure.pdf")
    output = tmp_path / "soft_mask_restore_failure_out.pdf"

    with patch(
        "file_compressor.pdfs._replace_pdf_image_preserving_soft_mask",
        side_effect=_SoftMaskRestoreError("Unable to restore PDF soft mask 99"),
    ):
        with pytest.raises(_SoftMaskRestoreError, match="soft mask 99"):
            compress_pdf(
                source,
                output,
                CompressionConfig(pdf_mode="text", compression_level=2, output_dir=tmp_path),
            )

    assert not output.exists()


def test_optimize_images_does_not_save_partial_soft_mask_replacement(tmp_path: Path):
    from unittest.mock import patch

    from file_compressor.pdfs import _SoftMaskRestoreError

    source = _make_pdf_with_soft_mask(tmp_path / "soft_mask_optimize_restore_failure.pdf")
    output = tmp_path / "soft_mask_optimize_restore_failure_out.pdf"

    with patch(
        "file_compressor.pdfs._replace_pdf_image_preserving_soft_mask",
        side_effect=_SoftMaskRestoreError("Unable to restore PDF soft mask 99"),
    ):
        with pytest.raises(_SoftMaskRestoreError, match="soft mask 99"):
            optimize_images_in_pdf(
                source,
                output,
                CompressionConfig(compression_level=2, output_dir=tmp_path),
            )

    assert not output.exists()


def test_compress_pdf_text_mode_keeps_text(tmp_path: Path):
    source = _make_pdf_with_image(tmp_path / "txt.pdf", pages=2)
    output = tmp_path / "txt_out.pdf"

    config = CompressionConfig(pdf_mode="text", output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()
    # Near-lossless pass must keep the text layer and not grow the file.
    assert _text_chars(output) > 0
    assert output.stat().st_size <= source.stat().st_size


def test_compress_pdf_text_mode_with_target_keeps_text(tmp_path: Path):
    source = _make_pdf_with_image(tmp_path / "txt_t.pdf", pages=2)
    output = tmp_path / "txt_t_out.pdf"
    original = source.stat().st_size
    target = int(original * 0.5)

    config = CompressionConfig(pdf_mode="text", target_bytes=target, output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()
    assert _text_chars(output) > 0  # text preserved, never rasterized
    assert output.stat().st_size <= target
    assert output.stat().st_size < original


def test_compress_pdf_text_mode_no_images(tmp_path: Path):
    # A text-only PDF (no raster images) must still succeed and keep its text.
    source = _make_pdf(tmp_path / "txt_only.pdf", pages=3)
    output = tmp_path / "txt_only_out.pdf"

    config = CompressionConfig(pdf_mode="text", output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()
    assert _text_chars(output) > 0


def test_compress_pdf_with_target_size(tmp_path: Path):
    source = _make_pdf(tmp_path / "target.pdf", pages=5, with_images=True)
    output = tmp_path / "target_out.pdf"

    config = CompressionConfig(
        target_bytes=50_000,
        pdf_mode="raster",
        pdf_dpi=100,
        quality=50,
        output_dir=tmp_path,
    )
    compress_pdf(source, output, config)

    assert output.exists()
    assert output.stat().st_size <= 60_000


def test_compress_pdf_auto_with_target_falls_back_to_raster(tmp_path: Path):
    source = _make_pdf(tmp_path / "fallback.pdf", pages=5, with_images=True)
    output = tmp_path / "fallback_out.pdf"

    config = CompressionConfig(
        target_bytes=30_000,
        pdf_mode="auto",
        pdf_dpi=100,
        quality=50,
        output_dir=tmp_path,
    )
    compress_pdf(source, output, config)

    assert output.exists()


def test_compress_pdf_auto_logs_failed_fallback_passes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    import logging
    from unittest.mock import patch

    source = _make_pdf(tmp_path / "fallback_errors.pdf")
    output = tmp_path / "fallback_errors_out.pdf"
    config = CompressionConfig(target_bytes=1, pdf_mode="auto", output_dir=tmp_path)

    with (
        caplog.at_level(logging.DEBUG, logger="file_compressor.pdfs"),
        patch("file_compressor.pdfs.optimize_pdf", side_effect=RuntimeError("optimize failed")),
        patch(
            "file_compressor.pdfs.compress_pdf_keep_text",
            side_effect=RuntimeError("keep-text failed"),
        ),
        patch(
            "file_compressor.pdfs.rasterize_pdf_to_target",
            side_effect=RuntimeError("raster failed"),
        ),
    ):
        compress_pdf(source, output, config)

    assert output.read_bytes() == source.read_bytes()
    assert "Optimize PDF pass failed" in caplog.text
    assert "Keep-text PDF pass failed" in caplog.text
    assert "Raster PDF pass failed" in caplog.text


def test_compress_pdf_auto_with_large_target_returns_optimized(tmp_path: Path):
    """Auto mode returns optimized result when it's within ±10% of target."""
    source = _make_pdf(tmp_path / "big_target.pdf", pages=3)
    output = tmp_path / "big_target_out.pdf"
    opt_size = optimize_pdf(source, tmp_path / "opt.pdf", CompressionConfig(output_dir=tmp_path)).stat().st_size

    config = CompressionConfig(
        target_bytes=int(opt_size * 1.05),
        pdf_mode="auto",
        pdf_dpi=120,
        quality=82,
        output_dir=tmp_path,
    )
    compress_pdf(source, output, config)
    assert output.exists()
    assert output.stat().st_size <= int(opt_size * 1.1)


def test_compress_pdf_grayscale_with_target(tmp_path: Path):
    source = _make_pdf(tmp_path / "gray.pdf", pages=3, with_images=True)
    output = tmp_path / "gray_out.pdf"

    config = CompressionConfig(
        target_bytes=50_000,
        pdf_mode="raster",
        pdf_dpi=80,
        quality=40,
        pdf_grayscale=True,
        output_dir=tmp_path,
    )
    compress_pdf(source, output, config)

    assert output.exists()
    assert output.stat().st_size <= 60_000


def test_compress_pdf_keep_metadata(tmp_path: Path):
    source = _make_pdf(tmp_path / "meta.pdf")
    output = tmp_path / "meta_out.pdf"

    config = CompressionConfig(pdf_mode="optimize", strip_metadata=False, output_dir=tmp_path)
    compress_pdf(source, output, config)

    assert output.exists()


def test_render_page_returns_png(tmp_path: Path):
    source = _make_pdf(tmp_path / "render.pdf", pages=3)
    png = render_page(source, 0)
    assert isinstance(png, bytes)
    assert png[:4] == b"\x89PNG"


def test_render_page_second_page(tmp_path: Path):
    source = _make_pdf(tmp_path / "render2.pdf", pages=3)
    png = render_page(source, 1)
    assert len(png) > 0
    assert png[:4] == b"\x89PNG"


def test_render_page_out_of_range(tmp_path: Path):
    source = _make_pdf(tmp_path / "render_oob.pdf", pages=2)
    with pytest.raises(ValueError, match="out of range"):
        render_page(source, 5)


def test_render_page_negative_index(tmp_path: Path):
    source = _make_pdf(tmp_path / "render_neg.pdf", pages=2)
    with pytest.raises(ValueError, match="out of range"):
        render_page(source, -1)


def test_fitz_import_error(tmp_path: Path):
    import builtins
    import sys
    from unittest.mock import patch
    from file_compressor.pdfs import _fitz

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No module named 'fitz'")
        return real_import(name, *args, **kwargs)

    saved = sys.modules.pop("fitz", None)
    try:
        with patch.object(builtins, "__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="PyMuPDF"):
                _fitz()
    finally:
        if saved is not None:
            sys.modules["fitz"] = saved


def test_rasterize_pdf_to_target_no_output(tmp_path: Path):
    from unittest.mock import patch
    from file_compressor.pdfs import rasterize_pdf_to_target

    source = _make_pdf(tmp_path / "src.pdf")
    output = tmp_path / "out.pdf"
    config = CompressionConfig(target_bytes=50000)
    with patch("file_compressor.pdfs._pdf_candidates", return_value=[]):
        with pytest.raises(RuntimeError, match="no output"):
            rasterize_pdf_to_target(source, output, config)


def test_rasterize_pdf_dst_open_failure(tmp_path: Path):
    """When fitz.open() for dst fails, src must still be closed."""
    from unittest.mock import MagicMock, patch
    from file_compressor.pdfs import rasterize_pdf

    source = _make_pdf(tmp_path / "src.pdf")
    output = tmp_path / "out.pdf"

    mock_src = MagicMock()
    mock_src.close = MagicMock()
    mock_fitz = MagicMock()
    mock_fitz.open.side_effect = [mock_src, RuntimeError("open dst failed")]

    with patch("file_compressor.pdfs._fitz", return_value=mock_fitz):
        with pytest.raises(RuntimeError, match="open dst failed"):
            rasterize_pdf(source, output, dpi=100, quality=70, grayscale=False, strip_metadata=False)

    mock_src.close.assert_called_once()
