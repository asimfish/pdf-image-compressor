from pathlib import Path

from file_compressor.storage import Storage


def test_add_and_list_pdfs(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("test.pdf", b"%PDF-1.4 fake content", 5, notes="hello")
    assert pdf.filename == "test.pdf"
    assert pdf.page_count == 5
    assert pdf.notes == "hello"

    pdfs = storage.list_pdfs()
    assert len(pdfs) == 1
    assert pdfs[0].id == pdf.id
    storage.close()


def test_get_pdf_and_path(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("doc.pdf", b"%PDF-1.4 content", 3)

    assert storage.get_pdf(pdf.id) is not None
    assert storage.get_pdf_path(pdf.id).exists()
    assert storage.get_pdf("nonexistent") is None
    storage.close()


def test_delete_pdf_removes_file(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("del.pdf", b"%PDF delete me", 1)
    path = storage.get_pdf_path(pdf.id)
    assert path.exists()

    assert storage.delete_pdf(pdf.id)
    assert not path.exists()
    assert storage.get_pdf(pdf.id) is None
    storage.close()


def test_add_and_list_versions(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("v.pdf", b"%PDF-1.4 original", 10)

    v1 = storage.add_version(
        pdf_id=pdf.id, label="500KB · Q70 · auto",
        file_data=b"%PDF compressed v1", quality=70,
        pdf_mode="auto", pdf_dpi=120, pdf_grayscale=False,
        target_bytes=500_000, compression_ratio=0.45,
    )
    v2 = storage.add_version(
        pdf_id=pdf.id, label="300KB · Q50 · raster",
        file_data=b"%PDF compressed v2", quality=50,
        pdf_mode="raster", pdf_dpi=100, pdf_grayscale=True,
        target_bytes=300_000, compression_ratio=0.65,
    )

    versions = storage.list_versions(pdf.id)
    assert len(versions) == 2
    ids = {v.id for v in versions}
    assert v1.id in ids
    assert v2.id in ids
    storage.close()


def test_delete_version(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("vd.pdf", b"%PDF", 1)
    v = storage.add_version(
        pdf_id=pdf.id, label="test", file_data=b"compressed",
        quality=80, pdf_mode="auto", pdf_dpi=120, pdf_grayscale=False,
        target_bytes=None, compression_ratio=None,
    )
    vpath = storage.get_version_path(v.id)
    assert vpath.exists()

    assert storage.delete_version(v.id)
    assert not vpath.exists()
    assert storage.list_versions(pdf.id) == []
    storage.close()


def test_delete_pdf_cascades_versions(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("cascade.pdf", b"%PDF", 1)
    storage.add_version(
        pdf_id=pdf.id, label="v1", file_data=b"c1",
        quality=80, pdf_mode="auto", pdf_dpi=120, pdf_grayscale=False,
        target_bytes=None, compression_ratio=None,
    )
    storage.delete_pdf(pdf.id)
    assert storage.list_versions(pdf.id) == []
    storage.close()


def test_update_notes(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("notes.pdf", b"%PDF", 1, notes="original")
    assert storage.update_notes(pdf.id, "updated")
    assert storage.get_pdf(pdf.id).notes == "updated"
    storage.close()


def test_update_notes_nonexistent(tmp_path: Path):
    storage = Storage(tmp_path)
    assert not storage.update_notes("nonexistent", "test")
    storage.close()


def test_pdf_best_versions(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("best.pdf", b"%PDF" * 100, 5)
    storage.add_version(
        pdf_id=pdf.id, label="v1", file_data=b"small",
        quality=50, pdf_mode="raster", pdf_dpi=100, pdf_grayscale=False,
        target_bytes=None, compression_ratio=0.7,
    )
    bests = storage.pdf_best_versions()
    assert pdf.id in bests
    assert bests[pdf.id]["best_ratio"] == 0.7
    assert bests[pdf.id]["best_size"] == len(b"small")
    storage.close()


def test_stats(tmp_path: Path):
    storage = Storage(tmp_path)
    storage.add_pdf("a.pdf", b"A" * 1000, 3)
    storage.add_pdf("b.pdf", b"B" * 2000, 5)

    stats = storage.stats()
    assert stats["pdf_count"] == 2
    assert stats["total_original_bytes"] == 3000
    assert stats["version_count"] == 0
    assert stats["total_saved_bytes"] == 0
    storage.close()


def test_stats_with_savings(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("big.pdf", b"X" * 10000, 5)
    storage.add_version(
        pdf_id=pdf.id, label="compressed", file_data=b"Y" * 3000,
        quality=50, pdf_mode="raster", pdf_dpi=100, pdf_grayscale=False,
        target_bytes=None, compression_ratio=0.7,
    )
    stats = storage.stats()
    assert stats["total_saved_bytes"] == 7000
    storage.close()


def test_file_on_disk(tmp_path: Path):
    storage = Storage(tmp_path)
    assert (tmp_path / "originals").is_dir()
    assert (tmp_path / "versions").is_dir()
    assert (tmp_path / "library.db").is_file()
    storage.close()
