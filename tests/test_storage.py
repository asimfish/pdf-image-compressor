from pathlib import Path
from typing import Optional

from file_compressor.storage import Storage, VersionParams


def _vp(
    pdf_id: str,
    label: str = "v1",
    file_data: bytes = b"compressed",
    quality: int = 80,
    pdf_mode: str = "auto",
    pdf_dpi: int = 120,
    pdf_grayscale: bool = False,
    target_bytes: Optional[int] = None,
    compression_ratio: Optional[float] = None,
    strip_metadata: bool = True,
) -> VersionParams:
    return VersionParams(
        pdf_id=pdf_id, label=label, file_data=file_data,
        quality=quality, pdf_mode=pdf_mode, pdf_dpi=pdf_dpi,
        pdf_grayscale=pdf_grayscale, target_bytes=target_bytes,
        compression_ratio=compression_ratio, strip_metadata=strip_metadata,
    )


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

    v1 = storage.add_version(_vp(
        pdf_id=pdf.id, label="500KB · Q70 · auto",
        file_data=b"%PDF compressed v1", quality=70,
        target_bytes=500_000, compression_ratio=0.45,
    ))
    v2 = storage.add_version(_vp(
        pdf_id=pdf.id, label="300KB · Q50 · raster",
        file_data=b"%PDF compressed v2", quality=50,
        pdf_mode="raster", pdf_dpi=100, pdf_grayscale=True,
        target_bytes=300_000, compression_ratio=0.65,
    ))

    versions = storage.list_versions(pdf.id)
    assert len(versions) == 2
    ids = {v.id for v in versions}
    assert v1.id in ids
    assert v2.id in ids
    storage.close()


def test_delete_version(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("vd.pdf", b"%PDF", 1)
    v = storage.add_version(_vp(pdf_id=pdf.id, label="test", file_data=b"compressed"))
    vpath = storage.get_version_path(v.id)
    assert vpath.exists()

    assert storage.delete_version(v.id)
    assert not vpath.exists()
    assert storage.list_versions(pdf.id) == []
    storage.close()


def test_delete_version_rollback_preserves_file(tmp_path: Path):
    import pytest

    storage = Storage(tmp_path)
    pdf = storage.add_pdf("vd.pdf", b"%PDF", 1)
    v = storage.add_version(_vp(pdf_id=pdf.id, label="test", file_data=b"compressed"))
    vpath = storage.get_version_path(v.id)
    assert vpath.exists()

    real_conn = storage._conn

    class FailingCommitConn:
        def __getattr__(self, name):
            if name == "commit":
                def fail():
                    raise RuntimeError("simulated db error")
                return fail
            return getattr(real_conn, name)

    storage._conn = FailingCommitConn()
    with pytest.raises(RuntimeError, match="simulated db error"):
        storage.delete_version(v.id)

    storage._conn = real_conn
    # After rollback, version record and file should still exist
    assert storage.get_version(v.id) is not None
    assert vpath.exists()
    storage.close()


def test_delete_pdf_cascades_versions(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("cascade.pdf", b"%PDF", 1)
    storage.add_version(_vp(pdf_id=pdf.id, label="v1", file_data=b"c1"))
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
    storage.add_version(_vp(
        pdf_id=pdf.id, label="compressed", file_data=b"Y" * 3000,
        quality=50, pdf_mode="raster", pdf_dpi=100, compression_ratio=0.7,
    ))
    stats = storage.stats()
    assert stats["total_saved_bytes"] == 7000
    storage.close()


def test_stats_compressed_bytes_uses_best_version(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("multi.pdf", b"X" * 10000, 5)
    storage.add_version(_vp(
        pdf_id=pdf.id, label="v1-big", file_data=b"A" * 8000,
        quality=80, compression_ratio=0.2,
    ))
    storage.add_version(_vp(
        pdf_id=pdf.id, label="v2-small", file_data=b"B" * 2000,
        quality=50, pdf_mode="raster", pdf_dpi=100, pdf_grayscale=True,
        compression_ratio=0.8,
    ))
    stats = storage.stats()
    assert stats["version_count"] == 2
    assert stats["total_original_bytes"] == 10000
    assert stats["total_compressed_bytes"] == 2000
    assert stats["total_saved_bytes"] == 8000
    storage.close()


def test_file_on_disk(tmp_path: Path):
    storage = Storage(tmp_path)
    assert (tmp_path / "originals").is_dir()
    assert (tmp_path / "versions").is_dir()
    assert (tmp_path / "library.db").is_file()
    storage.close()


def test_storage_close(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("ctx.pdf", b"%PDF", 1)
    assert storage.get_pdf(pdf.id) is not None
    storage.close()
    # Connection should be closed after calling close()


def test_batch_delete_pdfs(tmp_path: Path):
    storage = Storage(tmp_path)
    ids = []
    for name in ["a.pdf", "b.pdf", "c.pdf"]:
        pdf = storage.add_pdf(name, b"%PDF", 1)
        storage.add_version(_vp(pdf_id=pdf.id))
        ids.append(pdf.id)

    deleted = storage.batch_delete_pdfs(ids[:2])
    assert deleted == 2
    assert storage.get_pdf(ids[0]) is None
    assert storage.get_pdf(ids[1]) is None
    assert storage.get_pdf(ids[2]) is not None
    assert len(storage.list_pdfs()) == 1
    # Verify files cleaned up from disk
    for pdf_id in ids[:2]:
        assert not (tmp_path / "originals").joinpath(f"{pdf_id}.pdf").exists()
    assert len(list((tmp_path / "versions").iterdir())) == 1
    storage.close()


def test_batch_delete_pdfs_empty(tmp_path: Path):
    storage = Storage(tmp_path)
    assert storage.batch_delete_pdfs([]) == 0
    storage.close()


def test_list_pdfs_with_stats(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("stats.pdf", b"%PDF" * 100, 5)
    storage.add_version(_vp(pdf_id=pdf.id, compression_ratio=0.5))
    result = storage.list_pdfs_with_stats()
    assert len(result) == 1
    assert result[0].version_count == 1
    assert result[0].best_compression_ratio == 0.5
    assert result[0].best_compressed_size == len(b"compressed")
    storage.close()


def test_list_pdfs_with_stats_empty(tmp_path: Path):
    storage = Storage(tmp_path)
    assert storage.list_pdfs_with_stats() == []
    storage.close()


def test_batch_delete_with_nonexistent(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("real.pdf", b"%PDF", 1)
    deleted = storage.batch_delete_pdfs([pdf.id, "nonexistent"])
    assert deleted == 1
    assert storage.get_pdf(pdf.id) is None
    storage.close()


def test_get_pdf_path_nonexistent(tmp_path: Path):
    storage = Storage(tmp_path)
    assert storage.get_pdf_path("nonexistent") is None
    storage.close()


def test_get_version_path_nonexistent(tmp_path: Path):
    storage = Storage(tmp_path)
    assert storage.get_version_path("nonexistent") is None
    storage.close()


def test_delete_version_nonexistent(tmp_path: Path):
    storage = Storage(tmp_path)
    assert not storage.delete_version("nonexistent")
    storage.close()


def test_list_pdfs_with_stats_multiple(tmp_path: Path):
    storage = Storage(tmp_path)
    p1 = storage.add_pdf("a.pdf", b"A" * 100, 3)
    p2 = storage.add_pdf("b.pdf", b"B" * 200, 5)
    storage.add_version(_vp(
        pdf_id=p1.id, label="v1", file_data=b"compressed1", compression_ratio=0.5,
    ))
    result = storage.list_pdfs_with_stats()
    assert len(result) == 2
    ids = {r.id for r in result}
    assert p1.id in ids
    assert p2.id in ids
    p1_stats = next(r for r in result if r.id == p1.id)
    p2_stats = next(r for r in result if r.id == p2.id)
    assert p1_stats.version_count == 1
    assert p2_stats.version_count == 0
    storage.close()


def test_delete_pdf_cleans_version_files(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("doc.pdf", b"%PDF" * 100, 2)
    v1 = storage.add_version(_vp(
        pdf_id=pdf.id, label="v1", file_data=b"compressed1", compression_ratio=0.5,
    ))
    v2 = storage.add_version(_vp(
        pdf_id=pdf.id, label="v2", file_data=b"compressed2",
        quality=60, compression_ratio=0.7,
    ))
    v1_path = storage.get_version_path(v1.id)
    v2_path = storage.get_version_path(v2.id)
    assert v1_path is not None and v1_path.exists()
    assert v2_path is not None and v2_path.exists()

    storage.delete_pdf(pdf.id)

    assert not v1_path.exists()
    assert not v2_path.exists()
    assert storage.get_pdf(pdf.id) is None
    assert storage.list_versions(pdf.id) == []
    storage.close()


def test_batch_delete_rollback_on_error(tmp_path: Path):
    import pytest

    storage = Storage(tmp_path)
    p1 = storage.add_pdf("a.pdf", b"%PDF", 1)
    p2 = storage.add_pdf("b.pdf", b"%PDF", 1)

    real_conn = storage._conn

    class FailingCommitConn:
        def __getattr__(self, name):
            if name == "commit":
                def fail():
                    raise RuntimeError("simulated db error")
                return fail
            return getattr(real_conn, name)

    storage._conn = FailingCommitConn()
    with pytest.raises(RuntimeError, match="simulated db error"):
        storage.batch_delete_pdfs([p1.id, p2.id])

    storage._conn = real_conn
    # After rollback, both PDFs should still exist
    assert storage.get_pdf(p1.id) is not None
    assert storage.get_pdf(p2.id) is not None
    # Files should NOT have been deleted (rollback preserves DB, files deferred)
    assert (tmp_path / "originals").joinpath(f"{p1.id}.pdf").exists()
    assert (tmp_path / "originals").joinpath(f"{p2.id}.pdf").exists()
    storage.close()


def test_delete_pdf_rollback_preserves_files(tmp_path: Path):
    import pytest

    storage = Storage(tmp_path)
    pdf = storage.add_pdf("rollback.pdf", b"%PDF", 1)
    v = storage.add_version(_vp(
        pdf_id=pdf.id, label="v1", file_data=b"compressed", compression_ratio=0.5,
    ))
    pdf_path = storage.get_pdf_path(pdf.id)
    v_path = storage.get_version_path(v.id)
    assert pdf_path.exists()
    assert v_path.exists()

    real_conn = storage._conn

    class FailingCommitConn:
        def __getattr__(self, name):
            if name == "commit":
                def fail():
                    raise RuntimeError("simulated db error")
                return fail
            return getattr(real_conn, name)

    storage._conn = FailingCommitConn()
    with pytest.raises(RuntimeError, match="simulated db error"):
        storage.delete_pdf(pdf.id)

    storage._conn = real_conn
    # After rollback, PDF and files should still exist
    assert storage.get_pdf(pdf.id) is not None
    assert pdf_path.exists()
    assert v_path.exists()
    storage.close()


def test_version_strip_metadata_true(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("sm.pdf", b"%PDF", 1)
    v = storage.add_version(_vp(
        pdf_id=pdf.id, label="stripped", file_data=b"compressed",
        compression_ratio=0.5, strip_metadata=True,
    ))
    assert v.strip_metadata is True
    versions = storage.list_versions(pdf.id)
    assert versions[0].strip_metadata is True
    storage.close()


def test_version_strip_metadata_false(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("nosm.pdf", b"%PDF", 1)
    v = storage.add_version(_vp(
        pdf_id=pdf.id, label="kept", file_data=b"compressed",
        compression_ratio=0.5, strip_metadata=False,
    ))
    assert v.strip_metadata is False
    versions = storage.list_versions(pdf.id)
    assert versions[0].strip_metadata is False
    storage.close()


def test_version_strip_metadata_default_true(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("def.pdf", b"%PDF", 1)
    v = storage.add_version(_vp(
        pdf_id=pdf.id, label="default", file_data=b"compressed", compression_ratio=0.5,
    ))
    assert v.strip_metadata is True
    storage.close()


def test_migration_adds_strip_metadata_column(tmp_path: Path):
    """Test that an existing database without strip_metadata column gets migrated."""
    import sqlite3
    db_path = tmp_path / "library.db"
    (tmp_path / "originals").mkdir()
    (tmp_path / "versions").mkdir()
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE pdfs (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL, page_count INTEGER NOT NULL DEFAULT 0,
            upload_time TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE versions (
            id TEXT PRIMARY KEY, pdf_id TEXT NOT NULL REFERENCES pdfs(id) ON DELETE CASCADE,
            label TEXT NOT NULL, file_path TEXT NOT NULL, file_size INTEGER NOT NULL,
            quality INTEGER NOT NULL, pdf_mode TEXT NOT NULL DEFAULT 'auto',
            pdf_dpi INTEGER NOT NULL DEFAULT 120, pdf_grayscale INTEGER NOT NULL DEFAULT 0,
            target_bytes INTEGER, compression_ratio REAL, created_at TEXT NOT NULL
        );
    """)
    conn.execute("INSERT INTO pdfs VALUES ('p1','test.pdf','',100,1,'2024-01-01','')")
    conn.execute("INSERT INTO versions VALUES ('v1','p1','v1','',50,80,'auto',120,0,NULL,0.5,'2024-01-01')")
    conn.commit()
    conn.close()

    storage = Storage(tmp_path)
    versions = storage.list_versions("p1")
    assert len(versions) == 1
    assert versions[0].strip_metadata is True
    storage.close()


def test_add_pdf_cleans_file_on_db_error(tmp_path: Path):
    storage = Storage(tmp_path)
    originals = tmp_path / "originals"
    # Close DB so INSERT fails
    storage._conn.close()
    try:
        storage.add_pdf("fail.pdf", b"%PDF-1.4", 1)
    except Exception:
        pass
    assert not any(originals.iterdir())


def test_add_version_cleans_file_on_db_error(tmp_path: Path):
    storage = Storage(tmp_path)
    pdf = storage.add_pdf("ok.pdf", b"%PDF-1.4", 1)
    versions = tmp_path / "versions"
    # Close DB so INSERT fails
    storage._conn.close()
    try:
        storage.add_version(_vp(pdf_id=pdf.id, file_data=b"%PDF-1.4 compressed", quality=82, compression_ratio=0.5))
    except Exception:
        pass
    remaining = list(versions.iterdir())
    assert len(remaining) == 0


def test_batch_delete_succeeds_when_file_unlink_fails(tmp_path: Path):
    from unittest.mock import patch

    storage = Storage(tmp_path)
    pdf = storage.add_pdf("lock.pdf", b"%PDF", 1)
    storage.add_version(_vp(pdf_id=pdf.id, compression_ratio=0.5))

    def failing_unlink(self, *args, **kwargs):
        raise OSError("Permission denied")

    with patch.object(Path, "unlink", failing_unlink):
        deleted = storage.batch_delete_pdfs([pdf.id])

    assert deleted == 1
    # DB record deleted even though file unlink failed
    assert storage.get_pdf(pdf.id) is None
    # Version records also deleted (cascade)
    assert storage.list_versions(pdf.id) == []
    storage.close()


def test_delete_pdf_succeeds_when_file_unlink_fails(tmp_path: Path):
    from unittest.mock import patch

    storage = Storage(tmp_path)
    pdf = storage.add_pdf("lock.pdf", b"%PDF", 1)
    storage.add_version(_vp(pdf_id=pdf.id, compression_ratio=0.5))

    def failing_unlink(self, *args, **kwargs):
        raise OSError("Permission denied")

    with patch.object(Path, "unlink", failing_unlink):
        deleted = storage.delete_pdf(pdf.id)

    assert deleted is True
    assert storage.get_pdf(pdf.id) is None
    assert storage.list_versions(pdf.id) == []
    storage.close()


def test_delete_version_succeeds_when_file_unlink_fails(tmp_path: Path):
    from unittest.mock import patch

    storage = Storage(tmp_path)
    pdf = storage.add_pdf("lock.pdf", b"%PDF", 1)
    v = storage.add_version(_vp(pdf_id=pdf.id, compression_ratio=0.5))

    def failing_unlink(self, *args, **kwargs):
        raise OSError("Permission denied")

    with patch.object(Path, "unlink", failing_unlink):
        deleted = storage.delete_version(v.id)

    assert deleted is True
    assert storage.get_version(v.id) is None
    storage.close()


def test_context_manager(tmp_path: Path):
    with Storage(tmp_path) as storage:
        pdf = storage.add_pdf("test.pdf", b"%PDF-1.4 fake", 1)
        assert pdf.filename == "test.pdf"
    # Connection should be closed after exiting context
    import sqlite3
    try:
        storage._conn.execute("SELECT 1")
        assert False, "Connection should be closed"
    except sqlite3.ProgrammingError:
        pass


def test_migration_rejects_unsafe_table(tmp_path: Path):
    import pytest
    from unittest.mock import patch

    storage = Storage(tmp_path)
    # Patch _EXPECTED_COLUMNS to include a table NOT in _ALLOWED_MIGRATE_TABLES
    with patch.object(type(storage), "_EXPECTED_COLUMNS", (("unsafe_table", "col INTEGER"),)):
        with pytest.raises(ValueError, match="not in allowlist"):
            storage._migrate()
    storage.close()


def test_cleanup_orphans_removes_unreferenced_files(tmp_path: Path):
    storage = Storage(tmp_path)
    # Create a real PDF so DB has a record
    storage.add_pdf("test.pdf", b"A" * 100, 1)
    # Drop an orphan file into originals/
    orphan = storage._originals / "orphan.pdf"
    orphan.write_bytes(b"junk")
    # Drop an orphan file into versions/
    orphan_ver = storage._versions / "orphan_ver.pdf"
    orphan_ver.write_bytes(b"junk")
    assert orphan.exists()
    assert orphan_ver.exists()
    removed = storage.cleanup_orphans()
    assert removed == 2
    assert not orphan.exists()
    assert not orphan_ver.exists()
    # The real file should still be there
    real_files = list(storage._originals.iterdir())
    assert len(real_files) == 1
    storage.close()


def test_cleanup_orphans_no_orphans(tmp_path: Path):
    storage = Storage(tmp_path)
    storage.add_pdf("test.pdf", b"A" * 100, 1)
    removed = storage.cleanup_orphans()
    assert removed == 0
    storage.close()
