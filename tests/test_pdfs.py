from pathlib import Path

import fitz
import pytest

from file_compressor.models import CompressionConfig
from file_compressor.pdfs import (
    compress_pdf,
    compress_pdf_keep_text,
    optimize_images_in_pdf,
    optimize_pdf,
    rasterize_pdf,
    render_page,
)


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


def test_compress_pdf_fidelity_unreachable_target_keeps_resolution(tmp_path: Path):
    source = _make_pdf_with_image(tmp_path / "fidelity_target.pdf")
    output = tmp_path / "fidelity_target_out.pdf"
    with fitz.open(source) as doc:
        source_image = doc.extract_image(doc[0].get_images(full=True)[0][0])
    source_dimensions = (source_image["width"], source_image["height"])

    compress_pdf(
        source,
        output,
        CompressionConfig(pdf_mode="fidelity", target_bytes=1, output_dir=tmp_path),
    )

    assert output.exists()
    assert _text_chars(output) == _text_chars(source)
    with fitz.open(output) as doc:
        output_image = doc.extract_image(doc[0].get_images(full=True)[0][0])
    assert (output_image["width"], output_image["height"]) == source_dimensions


def test_auto_mode_prefers_slight_keep_text_overshoot_over_raster(tmp_path: Path, monkeypatch):
    import file_compressor.pdfs as pdfs_module

    source = tmp_path / "source.pdf"
    source.write_bytes(b"0" * 1000)
    output = tmp_path / "out.pdf"

    def fail_optimize(*args, **kwargs):
        raise RuntimeError("optimize unavailable")

    def fake_keep_text(_source, candidate, _config):
        candidate.write_bytes(b"1" * 600)
        return candidate

    def fail_raster(*args, **kwargs):
        raise AssertionError("raster fallback must not run for a slight overshoot")

    monkeypatch.setattr(pdfs_module, "optimize_pdf", fail_optimize)
    monkeypatch.setattr(pdfs_module, "compress_pdf_keep_text", fake_keep_text)
    monkeypatch.setattr(pdfs_module, "rasterize_pdf_to_target", fail_raster)

    compress_pdf(
        source,
        output,
        CompressionConfig(pdf_mode="auto", target_bytes=500, output_dir=tmp_path),
    )

    assert output.read_bytes() == b"1" * 600


def test_auto_mode_never_rasterizes_even_for_large_overshoot(tmp_path: Path, monkeypatch):
    import file_compressor.pdfs as pdfs_module

    source = tmp_path / "source.pdf"
    source.write_bytes(b"0" * 1000)
    output = tmp_path / "out.pdf"

    def fail_optimize(*args, **kwargs):
        raise RuntimeError("optimize unavailable")

    def fake_keep_text(_source, candidate, _config):
        candidate.write_bytes(b"1" * 800)
        return candidate

    def fail_raster(*args, **kwargs):
        raise AssertionError("auto mode must never rasterize implicitly")

    monkeypatch.setattr(pdfs_module, "optimize_pdf", fail_optimize)
    monkeypatch.setattr(pdfs_module, "compress_pdf_keep_text", fake_keep_text)
    monkeypatch.setattr(pdfs_module, "rasterize_pdf_to_target", fail_raster)

    compress_pdf(
        source,
        output,
        CompressionConfig(pdf_mode="auto", target_bytes=500, output_dir=tmp_path),
    )

    assert output.read_bytes() == b"1" * 800


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
        "file_compressor.pdfs._rewrite_image_object",
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


def test_compress_pdf_auto_with_target_keeps_text_even_when_missing_target(tmp_path: Path):
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
    assert _text_chars(output) > 0


def test_compress_pdf_auto_logs_failed_passes(
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
    ):
        compress_pdf(source, output, config)

    assert output.read_bytes() == source.read_bytes()
    assert "Optimize PDF pass failed" in caplog.text
    assert "Keep-text PDF pass failed" in caplog.text


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


# ---------------------------------------------------------------------------
# ICC colorspace preservation (regression: wide-gamut scans washed out after
# compression because replace_image rebinds images to a generic sRGB profile)
# ---------------------------------------------------------------------------


def _make_noise_jpeg(size: tuple[int, int] = (400, 300), mode: str = "RGB") -> bytes:
    """Poorly-compressible JPEG so a lower-quality re-encode is always smaller."""
    import io
    import random

    from PIL import Image

    width, height = size
    channels = len(mode)
    random.seed(7)
    with Image.new(mode, size) as img:
        px = img.load()
        for y in range(0, height, 4):
            for x in range(0, width, 4):
                c = tuple(random.randint(0, 255) for _ in range(channels))
                for dy in range(4):
                    for dx in range(4):
                        if x + dx < width and y + dy < height:
                            px[x + dx, y + dy] = c
        with io.BytesIO() as buf:
            img.save(buf, format="JPEG", quality=95)
            return buf.getvalue()


def _make_pdf_with_icc_image(path: Path) -> tuple[Path, bytes, int]:
    """PDF whose embedded JPEG is bound to an ICCBased colorspace by reference,
    the way scanner/notes-app exports (e.g. Display P3) arrive in practice.

    Returns the path, the raw ICC profile bytes and the original image size.
    """
    from PIL import ImageCms

    data = _make_noise_jpeg()
    icc_bytes = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()

    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 60), "icc regression", fontsize=14)
        page.insert_image(fitz.Rect(50, 100, 450, 400), stream=data)
        img_xref = page.get_images(full=True)[0][0]

        icc_xref = doc.get_new_xref()
        doc.update_object(icc_xref, "<< /N 3 >>")
        doc.update_stream(icc_xref, icc_bytes, new=True)
        cs_xref = doc.get_new_xref()
        doc.update_object(cs_xref, f"[/ICCBased {icc_xref} 0 R]")
        doc.xref_set_key(img_xref, "ColorSpace", f"{cs_xref} 0 R")
        doc.save(path)
    return path, icc_bytes, len(data)


def _embedded_image_icc(path: Path) -> tuple[bytes | None, int]:
    """Return (ICC profile bytes or None, embedded image size) for page 1."""
    with fitz.open(path) as doc:
        img_xref = doc[0].get_images(full=True)[0][0]
        info = doc.extract_image(img_xref)
        cs_type, cs_value = doc.xref_get_key(img_xref, "ColorSpace")
        if cs_type != "xref":
            return None, len(info["image"])
        cs_obj = doc.xref_object(int(cs_value.split()[0]), compressed=True)
        if "/ICCBased" not in cs_obj:
            return None, len(info["image"])
        icc_ref = int(cs_obj.strip("[]").split()[1])
        return doc.xref_stream(icc_ref), len(info["image"])


def test_keep_text_compression_preserves_icc_colorspace(tmp_path: Path):
    source, icc_bytes, original_len = _make_pdf_with_icc_image(tmp_path / "icc.pdf")
    output = tmp_path / "icc_out.pdf"

    config = CompressionConfig(compression_level=3, output_dir=tmp_path)
    compress_pdf_keep_text(source, output, config)

    restored_icc, new_len = _embedded_image_icc(output)
    assert new_len < original_len, "image should have been re-encoded"
    assert restored_icc == icc_bytes


def test_optimize_images_preserves_icc_colorspace(tmp_path: Path):
    source, icc_bytes, original_len = _make_pdf_with_icc_image(tmp_path / "icc_opt.pdf")
    output = tmp_path / "icc_opt_out.pdf"

    config = CompressionConfig(compression_level=3, output_dir=tmp_path)
    optimize_images_in_pdf(source, output, config)

    restored_icc, new_len = _embedded_image_icc(output)
    assert new_len < original_len, "image should have been re-encoded"
    assert restored_icc == icc_bytes


def test_keep_text_compression_drops_icc_after_component_change(tmp_path: Path):
    """A CMYK image flattened to RGB must NOT keep its 4-component ICC entry."""
    data = _make_noise_jpeg(mode="CMYK")
    source = tmp_path / "cmyk.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_image(fitz.Rect(50, 100, 450, 400), stream=data)
        doc.save(source)

    output = tmp_path / "cmyk_out.pdf"
    config = CompressionConfig(compression_level=4, output_dir=tmp_path)
    compress_pdf_keep_text(source, output, config)

    with fitz.open(output) as doc:
        img_xref = doc[0].get_images(full=True)[0][0]
        info = doc.extract_image(img_xref)
        # the re-encoded image is RGB, so a stale CMYK colorspace would corrupt it
        assert info["colorspace"] == 3
        assert "CMYK" not in info["cs-name"]
        # the page must still render
        assert doc[0].get_pixmap(alpha=False).samples


def test_keep_text_target_refines_quality_between_ladder_rungs(tmp_path: Path):
    """With a byte budget, the search must not settle for the coarse ladder rung
    when several JPEG quality points of budget headroom remain."""
    from file_compressor.pdfs import _recompress_images_keep_text, compress_pdf_keep_text

    source = _make_pdf_with_image(tmp_path / "refine.pdf", pages=2)
    rung_hi = tmp_path / "rung92.pdf"
    rung_lo = tmp_path / "rung85.pdf"
    _recompress_images_keep_text(source, rung_hi, 1.0, 92, False)
    _recompress_images_keep_text(source, rung_lo, 1.0, 85, False)
    size_hi = rung_hi.stat().st_size
    size_lo = rung_lo.stat().st_size
    assert size_lo < size_hi

    target = (size_lo + size_hi) // 2
    output = tmp_path / "refined.pdf"
    config = CompressionConfig(target_bytes=target, output_dir=tmp_path)
    compress_pdf_keep_text(source, output, config)

    out_size = output.stat().st_size
    assert out_size <= target
    assert out_size > size_lo, "refinement should beat the coarse q85 rung"
    assert _text_chars(output) == _text_chars(source)


def _make_pdf_with_indexed_image(path: Path) -> Path:
    """PDF whose image is an 8-bit /Indexed palette over DeviceRGB.

    Dithered gradient indices are high-entropy (poor Flate) while the decoded
    picture is smooth (good JPEG), so the re-encoder always replaces it.
    """
    import random

    from PIL import Image

    width = height = 400
    random.seed(11)
    with Image.new("RGB", (width, height)) as img:
        px = img.load()
        for y in range(height):
            for x in range(width):
                px[x, y] = (
                    min(255, x * 255 // width + random.randint(-12, 12)) % 256,
                    y * 255 // height,
                    (x + y) * 255 // (width + height),
                )
        palette_img = img.quantize(colors=256, dither=Image.Dither.FLOYDSTEINBERG)
        indices = palette_img.tobytes()
        lookup = bytes(palette_img.getpalette()[:768])

    with fitz.open() as doc:
        page = doc.new_page()
        placeholder = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), 0)
        page.insert_image(fitz.Rect(50, 50, 450, 450), pixmap=placeholder)
        xref = page.get_images(full=True)[0][0]
        doc.update_object(
            xref,
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height}"
            f" /BitsPerComponent 8 /ColorSpace [/Indexed /DeviceRGB 255 <{lookup.hex()}>] >>",
        )
        doc.update_stream(xref, indices, new=True, compress=1)
        doc.save(path)
    return path


def _render_mean_abs_diff(a: Path, b: Path) -> float:
    with fitz.open(a) as da, fitz.open(b) as db:
        pa = da[0].get_pixmap(alpha=False)
        pb = db[0].get_pixmap(alpha=False)
        assert (pa.width, pa.height, pa.n) == (pb.width, pb.height, pb.n)
        sa, sb = pa.samples, pb.samples
    return sum(abs(x - y) for x, y in zip(sa, sb)) / len(sa)


def test_keep_text_compression_never_reattaches_indexed_colorspace(tmp_path: Path):
    """extract_image decodes palette images to RGB samples; re-attaching the
    /Indexed entry to the re-encoded JPEG would remap every pixel through the
    palette and scramble the colors (mean render diff ~25/255 vs ~1.5)."""
    source = _make_pdf_with_indexed_image(tmp_path / "indexed.pdf")
    output = tmp_path / "indexed_out.pdf"
    compress_pdf_keep_text(source, output, CompressionConfig(compression_level=1, output_dir=tmp_path))

    with fitz.open(output) as doc:
        info = doc.extract_image(doc[0].get_images(full=True)[0][0])
    assert info["ext"] == "jpeg", "palette image should have been re-encoded"
    assert "Indexed" not in info["cs-name"]
    assert info["colorspace"] == 3
    assert _render_mean_abs_diff(source, output) < 8


class _FakeColorspaceDoc:
    def __init__(self, cs_type: str, cs_value: str, objects: dict[int, str] | None = None):
        self._entry = (cs_type, cs_value)
        self._objects = objects or {}

    def is_stream(self, xref: int) -> bool:
        return False

    def xref_get_key(self, xref: int, key: str) -> tuple[str, str]:
        assert key == "ColorSpace"
        return self._entry

    def xref_object(self, xref: int, *, compressed: bool) -> str:
        return self._objects[xref]


@pytest.mark.parametrize(
    ("cs_type", "cs_value", "objects", "components", "mode", "expected"),
    [
        # ICC by reference, component count intact -> restore
        ("xref", "5 0 R", {5: "[/ICCBased 10 0 R]"}, 3, "RGB", "5 0 R"),
        ("xref", "5 0 R", {5: "[/ICCBased 10 0 R]"}, 1, "L", "5 0 R"),
        # inline calibrated spaces keep their meaning for the same components
        ("array", "[/CalRGB<</WhitePoint[.95 1 1.09]>>]", None, 3, "RGB", "[/CalRGB<</WhitePoint[.95 1 1.09]>>]"),
        ("array", "[/CalGray<</WhitePoint[.95 1 1.09]>>]", None, 1, "L", "[/CalGray<</WhitePoint[.95 1 1.09]>>]"),
        # component count changed (CMYK ICC flattened to RGB, gray ICC on RGB)
        ("xref", "5 0 R", {5: "[/ICCBased 10 0 R]"}, 4, "RGB", None),
        ("xref", "5 0 R", {5: "[/ICCBased 10 0 R]"}, 1, "RGB", None),
        # decoded-to-base-space families must never be re-attached
        ("array", "[/Indexed /DeviceRGB 255 <00>]", None, 1, "L", None),
        ("array", "[/Indexed /DeviceGray 255 <00>]", None, 1, "L", None),
        ("xref", "7 0 R", {7: "[/Indexed 9 0 R 58 49 0 R]"}, 1, "L", None),
        ("array", "[/Lab<</WhitePoint[.95 1 1.09]>>]", None, 3, "RGB", None),
        ("array", "[/Separation /Spot /DeviceCMYK 12 0 R]", None, 1, "L", None),
        ("array", "[/DeviceN [/A /B /C] /DeviceRGB 12 0 R]", None, 3, "RGB", None),
        # plain names carry no profile worth restoring
        ("name", "/DeviceRGB", None, 3, "RGB", None),
        ("null", "null", None, 3, "RGB", None),
    ],
)
def test_restorable_colorspace_families(cs_type, cs_value, objects, components, mode, expected):
    from file_compressor.pdfs import _restorable_colorspace

    doc = _FakeColorspaceDoc(cs_type, cs_value, objects)
    assert _restorable_colorspace(doc, 42, components, mode) == expected


def test_fidelity_mode_honors_reachable_target_below_near_lossless(tmp_path: Path):
    """Regression: the default mode used to give up on the budget as soon as
    q92 did not fit and return a near-lossless file far above the target."""
    from file_compressor.pdfs import _recompress_images_keep_text

    source = _make_pdf_with_image(tmp_path / "fidelity_budget.pdf", pages=2)
    q92 = tmp_path / "q92.pdf"
    q70 = tmp_path / "q70.pdf"
    _recompress_images_keep_text(source, q92, 1.0, 92, False)
    _recompress_images_keep_text(source, q70, 1.0, 70, False)
    size_q92 = q92.stat().st_size
    size_q70 = q70.stat().st_size
    assert size_q70 < size_q92
    target = (size_q70 + size_q92) // 2

    output = tmp_path / "fidelity_budget_out.pdf"
    compress_pdf(source, output, CompressionConfig(pdf_mode="fidelity", target_bytes=target, output_dir=tmp_path))

    out_size = output.stat().st_size
    assert out_size <= target, "fidelity mode must honor a reachable byte budget"
    assert out_size > size_q70, "should keep the highest quality that fits, not jump to a low rung"
    assert _text_chars(output) == _text_chars(source)
    with fitz.open(source) as src_doc, fitz.open(output) as out_doc:
        src_img = src_doc.extract_image(src_doc[0].get_images(full=True)[0][0])
        out_img = out_doc.extract_image(out_doc[0].get_images(full=True)[0][0])
    assert (out_img["width"], out_img["height"]) == (src_img["width"], src_img["height"])


def test_fidelity_mode_keeps_native_resolution_of_screen_dpi_images(tmp_path: Path):
    """The test image is placed at ~144 DPI, below every DPI cap on the ladder,
    so a budget under the lowest same-resolution rung must be met by quality
    alone while the pixel dimensions stay untouched."""
    from file_compressor.pdfs import _recompress_images_keep_text

    source = _make_pdf_with_image(tmp_path / "fidelity_tight.pdf", pages=2)
    lowest_full_res = tmp_path / "q70.pdf"
    _recompress_images_keep_text(source, lowest_full_res, 1.0, 70, False)
    target = int(lowest_full_res.stat().st_size * 0.9)

    output = tmp_path / "fidelity_tight_out.pdf"
    compress_pdf(source, output, CompressionConfig(pdf_mode="fidelity", target_bytes=target, output_dir=tmp_path))

    assert output.stat().st_size <= target
    assert _text_chars(output) == _text_chars(source)
    with fitz.open(source) as src_doc, fitz.open(output) as out_doc:
        src_img = src_doc.extract_image(src_doc[0].get_images(full=True)[0][0])
        out_img = out_doc.extract_image(out_doc[0].get_images(full=True)[0][0])
    assert (out_img["width"], out_img["height"]) == (src_img["width"], src_img["height"])


def _make_pdf_with_mixed_dpi_images(path: Path) -> Path:
    """One oversampled photo (2000 px drawn 2.5 in wide = 800 DPI) next to a
    small icon drawn at its native 72 DPI."""
    import io
    import random

    from PIL import Image

    random.seed(5)

    def noisy(width: int, height: int) -> bytes:
        with Image.new("RGB", (width, height)) as img:
            px = img.load()
            for y in range(0, height, 4):
                for x in range(0, width, 4):
                    c = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                    for dy in range(4):
                        for dx in range(4):
                            if x + dx < width and y + dy < height:
                                px[x + dx, y + dy] = c
            with io.BytesIO() as buf:
                img.save(buf, format="JPEG", quality=95)
                return buf.getvalue()

    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 60), "mixed dpi", fontsize=14)
        page.insert_image(fitz.Rect(50, 100, 230, 235), stream=noisy(2000, 1500))
        page.insert_image(fitz.Rect(300, 100, 400, 180), stream=noisy(100, 80))
        doc.save(path)
    return path


def test_target_search_downscales_only_oversampled_images(tmp_path: Path):
    """Under a tight budget the 800 DPI photo must lose pixels while the icon
    drawn at native resolution keeps every one of them."""
    from file_compressor.pdfs import _recompress_images_keep_text, compress_pdf_keep_text

    source = _make_pdf_with_mixed_dpi_images(tmp_path / "mixed.pdf")
    lowest_full_res = tmp_path / "q70.pdf"
    _recompress_images_keep_text(source, lowest_full_res, 1.0, 70, False)
    target = lowest_full_res.stat().st_size // 2

    output = tmp_path / "mixed_out.pdf"
    compress_pdf_keep_text(source, output, CompressionConfig(target_bytes=target, output_dir=tmp_path))

    assert output.stat().st_size <= target
    assert _text_chars(output) == _text_chars(source)
    with fitz.open(output) as doc:
        images = {}
        for img in doc[0].get_images(full=True):
            info = doc.extract_image(img[0])
            images[(info["width"] > 500)] = (info["width"], info["height"])
    big, small = images[True], images[False]
    assert big[0] < 2000, "oversampled photo should have been downscaled"
    assert big[0] >= 600, "a 300 DPI cap keeps at least 750 px for a 2.5 in placement"
    assert small == (100, 80), "native-resolution icon must not be resampled"


def test_refine_between_rungs_bisects_quality_at_same_scale():
    from file_compressor.pdfs import _KeepTextAttempt, _refine_between_rungs, _Rung

    # size model: 1000 bytes per quality point, budget admits up to q82
    probed: list[_Rung] = []

    def probe(rung: _Rung) -> _KeepTextAttempt:
        probed.append(rung)
        return _KeepTextAttempt(Path(f"q{rung.quality}"), rung.quality * 1000, rung)

    best = _refine_between_rungs(_Rung(1.0, 78), _Rung(1.0, 85), 82_000, probe)
    assert best is not None and best.rung.quality == 82
    assert all(r.scale == 1.0 and r.max_dpi is None for r in probed)
    assert {r.quality for r in probed} <= set(range(79, 85))


def test_refine_between_rungs_walks_mixed_scale_quality_segment():
    from file_compressor.pdfs import _KeepTextAttempt, _refine_between_rungs, _Rung

    # size grows with pixel count (scale^2) and quality; budget sits inside the gap
    def size_of(rung: _Rung) -> int:
        return int(1_000_000 * rung.scale * rung.scale * (rung.quality / 70))

    probed: list[_Rung] = []

    def probe(rung: _Rung) -> _KeepTextAttempt:
        probed.append(rung)
        return _KeepTextAttempt(Path("p"), size_of(rung), rung)

    fit, over = _Rung(0.85, 62), _Rung(1.0, 70)
    target = (size_of(fit) + size_of(over)) // 2
    best = _refine_between_rungs(fit, over, target, probe)

    assert best is not None
    assert best.size <= target
    assert best.size > size_of(fit), "refinement must use budget the coarse rung left unused"
    assert 0.85 < best.rung.scale < 1.0 and 62 < best.rung.quality < 70
    assert len(probed) <= 4
    assert all(0.85 <= r.scale <= 1.0 and 62 <= r.quality <= 70 for r in probed)


def test_refine_between_rungs_interpolates_dpi_cap():
    """Between a capped rung and an uncapped one the bisection must relax the
    cap (toward twice the fitting cap) together with the quality, never drop
    it, and every probe must stay inside the gap."""
    from file_compressor.pdfs import _KeepTextAttempt, _refine_between_rungs, _Rung

    def size_of(rung: _Rung) -> int:
        cap = rung.max_dpi or 600
        return int(1_000_000 * (cap / 600) ** 2 * (rung.quality / 70))

    probed: list[_Rung] = []

    def probe(rung: _Rung) -> _KeepTextAttempt:
        probed.append(rung)
        return _KeepTextAttempt(Path("p"), size_of(rung), rung)

    fit, over = _Rung(1.0, 66, 300), _Rung(1.0, 70)
    target = (size_of(fit) + size_of(over)) // 2
    best = _refine_between_rungs(fit, over, target, probe)

    assert best is not None and best.size <= target
    assert best.size > size_of(fit)
    assert all(r.scale == 1.0 and 300 <= r.max_dpi <= 600 and 66 <= r.quality <= 70 for r in probed)
    assert best.rung.max_dpi > 300

    # two capped rungs interpolate between the caps
    probed.clear()
    best = _refine_between_rungs(_Rung(1.0, 62, 250), _Rung(1.0, 66, 300), 10**12, probe)
    assert best is not None
    assert all(250 <= r.max_dpi <= 300 and 62 <= r.quality <= 66 for r in probed)


def test_refine_between_rungs_returns_none_when_nothing_fits():
    from file_compressor.pdfs import _KeepTextAttempt, _refine_between_rungs, _Rung

    def probe(rung: _Rung) -> _KeepTextAttempt:
        return _KeepTextAttempt(Path("p"), 10**9, rung)

    assert _refine_between_rungs(_Rung(1.0, 78), _Rung(1.0, 85), 1, probe) is None
    assert _refine_between_rungs(_Rung(0.85, 62), _Rung(1.0, 70), 1, probe) is None
    assert _refine_between_rungs(_Rung(1.0, 66, 300), _Rung(1.0, 70), 1, probe) is None


def test_keep_text_target_refines_upward_when_top_rung_fits(tmp_path: Path):
    """A generous budget must not stop at the ladder's top rung (q92) when
    q93-q95 still fit."""
    from file_compressor.pdfs import _recompress_images_keep_text, compress_pdf_keep_text

    source = _make_pdf_with_image(tmp_path / "generous.pdf", pages=2)
    q92 = tmp_path / "q92.pdf"
    q95 = tmp_path / "q95.pdf"
    _recompress_images_keep_text(source, q92, 1.0, 92, False)
    _recompress_images_keep_text(source, q95, 1.0, 95, False)
    size_q92, size_q95 = q92.stat().st_size, q95.stat().st_size
    assert size_q92 < size_q95

    output = tmp_path / "generous_out.pdf"
    compress_pdf_keep_text(source, output, CompressionConfig(target_bytes=size_q95 + 10, output_dir=tmp_path))

    out_size = output.stat().st_size
    assert out_size <= size_q95 + 10
    assert out_size > size_q92, "should climb above the q92 rung when the budget allows"


def test_closest_without_waste_prefers_quality_within_tolerance():
    from file_compressor.pdfs import _KeepTextAttempt, _Rung, _closest_without_waste

    # every rung misses the budget; 13.0 MB is the floor (vector content)
    attempts = [
        _KeepTextAttempt(Path("q70"), 19_000_000, _Rung(1.0, 70)),
        _KeepTextAttempt(Path("q58"), 15_000_000, _Rung(1.0, 58, 200)),
        _KeepTextAttempt(Path("q50"), 14_100_000, _Rung(1.0, 50, 150)),
        _KeepTextAttempt(Path("q40"), 13_600_000, _Rung(1.0, 40, 110)),
        _KeepTextAttempt(Path("q30"), 13_200_000, _Rung(1.0, 30, 80)),
        _KeepTextAttempt(Path("q24"), 13_000_000, _Rung(1.0, 24, 72)),
    ]
    chosen = _closest_without_waste(attempts)
    # q50 is 8.5% above the floor (allowed); q58 is 15% above (rejected)
    assert chosen is not None and chosen.rung.quality == 50
    assert _closest_without_waste([]) is None
    assert _closest_without_waste(attempts[:1]) is attempts[0]


def test_keep_text_unreachable_target_does_not_crush_images_for_nothing(tmp_path: Path, monkeypatch):
    """With an unreachable budget the fallback must not be the lowest rung when
    a much better rung is only marginally larger."""
    import file_compressor.pdfs as pdfs_module

    source = _make_pdf(tmp_path / "src.pdf", pages=1)
    sizes = {92: 1900, 85: 1500, 78: 1410, 70: 1360, 66: 1330, 62: 1310, 58: 1300, 54: 1290}

    def fake_encode(self, output, scale, quality, max_dpi=None):
        output.write_bytes(b"x" * sizes.get(quality, 1280))
        return output

    monkeypatch.setattr(pdfs_module._KeepTextSession, "encode", fake_encode)
    output = tmp_path / "out.pdf"
    pdfs_module.compress_pdf_keep_text(source, output, CompressionConfig(target_bytes=100, output_dir=tmp_path))
    # floor is 1280 bytes; anything up to 1408 qualifies, so q78 (1410) is out and q70 (1360) wins
    assert output.stat().st_size == 1360


@pytest.mark.parametrize("fit_at", [0, 1, 2, 5, 9, 13])
def test_first_fitting_rung_finds_boundary_with_few_probes(fit_at: int):
    from file_compressor.pdfs import _first_fitting_rung

    count = 14
    sizes = [1000 - 50 * i for i in range(count)]  # strictly decreasing
    target = sizes[fit_at]
    probed: list[int] = []

    def size_of(index: int) -> int:
        probed.append(index)
        return sizes[index]

    assert _first_fitting_rung(count, target, size_of) == fit_at
    assert len(set(probed)) <= 8, probed
    if fit_at:
        assert fit_at - 1 in probed, "the failing neighbour must be encoded for refinement"


def test_first_fitting_rung_returns_none_when_nothing_fits():
    from file_compressor.pdfs import _first_fitting_rung

    sizes = [1000 - 50 * i for i in range(14)]
    probed: list[int] = []

    def size_of(index: int) -> int:
        probed.append(index)
        return sizes[index]

    assert _first_fitting_rung(14, 1, size_of) is None
    assert 13 in probed and len(set(probed)) <= 6
    assert _first_fitting_rung(0, 1, size_of) is None


def _render_page_samples(path: Path, page: int = 0, zoom: float = 0.5):
    """Render a page to raw RGB bytes via MuPDF (proxy for viewer output)."""
    with fitz.open(path) as doc:
        pm = doc[page].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pm.samples, pm.width, pm.height


def test_keep_text_image_object_has_no_null_dict_entries(tmp_path: Path):
    """Regression: page.replace_image left `/Interpolate null` etc. behind, which
    Ghostscript type-checks and then drops the whole image (blank page). The
    in-place rewrite must never emit a null dictionary value."""
    source = _make_pdf_with_image(tmp_path / "nulls.pdf", pages=2)
    # give the image a boolean /Interpolate so the original has a value to lose
    with fitz.open(source) as doc:
        for page in doc:
            for img in page.get_images(full=True):
                doc.xref_set_key(img[0], "Interpolate", "true")
        doc.saveIncr()

    output = tmp_path / "nulls_out.pdf"
    compress_pdf_keep_text(source, output, CompressionConfig(compression_level=3, output_dir=tmp_path))

    with fitz.open(output) as doc:
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                for key in doc.xref_get_keys(xref):
                    value_type, _ = doc.xref_get_key(xref, key)
                    assert value_type != "null", f"{key} is null on xref {xref}"


def test_keep_text_preserves_carried_dictionary_keys(tmp_path: Path):
    """Rendering hints and structure links that still apply after re-encoding
    must be kept, not silently dropped, by the in-place rewrite."""
    source = _make_pdf_with_image(tmp_path / "carry.pdf", pages=2)
    with fitz.open(source) as doc:
        img_xref = doc[0].get_images(full=True)[0][0]
        doc.xref_set_key(img_xref, "Interpolate", "true")
        doc.xref_set_key(img_xref, "Intent", "/Perceptual")
        doc.saveIncr()

    output = tmp_path / "carry_out.pdf"
    compress_pdf_keep_text(source, output, CompressionConfig(compression_level=4, output_dir=tmp_path))

    with fitz.open(output) as doc:
        img_xref = doc[0].get_images(full=True)[0][0]
        assert doc.xref_get_key(img_xref, "Interpolate") == ("bool", "true")
        assert doc.xref_get_key(img_xref, "Intent") == ("name", "/Perceptual")
        assert doc.extract_image(img_xref)["ext"] == "jpeg"


def test_keep_text_rewrite_preserves_soft_mask_and_renders(tmp_path: Path):
    """The in-place rewrite must keep an external /SMask attached and the page
    must still render (the transparent region stays transparent)."""
    source = _make_pdf_with_soft_mask(tmp_path / "smask.pdf")
    output = tmp_path / "smask_out.pdf"
    compress_pdf_keep_text(source, output, CompressionConfig(compression_level=3, output_dir=tmp_path))

    with fitz.open(output) as doc:
        assert doc[0].get_images(full=True)[0][1] > 0  # smask xref still set
        assert doc[0].get_pixmap(alpha=False).samples  # renders without error


def test_keep_text_parallel_matches_serial_output(tmp_path: Path, monkeypatch):
    """The thread pool must not change the result: a single worker and the
    default pool must produce byte-identical documents."""
    import file_compressor.pdfs as pdfs_module

    source = _make_pdf_with_image(tmp_path / "parallel.pdf", pages=4)

    serial = tmp_path / "serial.pdf"
    monkeypatch.setattr(pdfs_module, "_ENCODE_WORKERS", 1)
    compress_pdf_keep_text(source, serial, CompressionConfig(compression_level=3, output_dir=tmp_path))

    parallel = tmp_path / "parallel_out.pdf"
    monkeypatch.setattr(pdfs_module, "_ENCODE_WORKERS", 8)
    compress_pdf_keep_text(source, parallel, CompressionConfig(compression_level=3, output_dir=tmp_path))

    assert _render_page_samples(serial) == _render_page_samples(parallel)


def test_keep_text_session_reuses_extraction_across_rungs(tmp_path: Path):
    """The target search must extract each image once and reuse it for every
    rung it encodes, not re-open and re-extract per rung."""
    import file_compressor.pdfs as pdfs_module

    source = _make_pdf_with_image(tmp_path / "session.pdf", pages=3)
    calls = {"n": 0}
    original_init = pdfs_module._KeepTextSession.__init__

    def counting_init(self, src, strip):
        calls["n"] += 1
        original_init(self, src, strip)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pdfs_module._KeepTextSession, "__init__", counting_init)
    try:
        q92 = tmp_path / "q92.pdf"
        q70 = tmp_path / "q70.pdf"
        pdfs_module._recompress_images_keep_text(source, q92, 1.0, 92, False)
        pdfs_module._recompress_images_keep_text(source, q70, 1.0, 70, False)
        target = (q92.stat().st_size + q70.stat().st_size) // 2
        calls["n"] = 0
        output = tmp_path / "session_out.pdf"
        pdfs_module.compress_pdf_keep_text(source, output, CompressionConfig(target_bytes=target, output_dir=tmp_path))
        # one session for the whole multi-rung + refinement search
        assert calls["n"] == 1
    finally:
        monkeypatch.undo()
