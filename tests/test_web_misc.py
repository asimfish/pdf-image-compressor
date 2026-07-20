from pathlib import Path

from fastapi.testclient import TestClient

from file_compressor.web import app, init_storage

from conftest import make_test_pdf


def _client(tmp_path: Path) -> TestClient:
    init_storage(tmp_path)
    return TestClient(app)

def test_update_notes(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "n.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("n.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.put(f"/api/pdfs/{pdf_id}/notes", json={"notes": "hello world"})
    assert resp.status_code == 200
    pdfs = client.get("/api/pdfs").json()
    assert pdfs[0]["notes"] == "hello world"




def test_update_notes_accepts_max_length(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "ml.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("ml.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.put(f"/api/pdfs/{pdf_id}/notes", json={"notes": "x" * 5000})
    assert resp.status_code == 200




def test_update_notes_clear(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "cl.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cl.pdf", f, "application/pdf")}, data={"notes": "initial"})
    pdf_id = upload.json()["id"]
    resp = client.put(f"/api/pdfs/{pdf_id}/notes", json={"notes": ""})
    assert resp.status_code == 200
    pdfs = client.get("/api/pdfs").json()
    assert pdfs[0]["notes"] == ""




def test_update_notes_rejects_too_long(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "nl.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("nl.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.put(f"/api/pdfs/{pdf_id}/notes", json={"notes": "x" * 5001})
    assert resp.status_code == 422




def test_update_notes_nonexistent_pdf(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.put("/api/pdfs/nonexistent/notes", json={"notes": "hello"})
    assert resp.status_code == 404




def test_delete_pdf(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "d.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("d.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.delete(f"/api/pdfs/{pdf_id}")
    assert resp.status_code == 200
    assert client.get("/api/pdfs").json() == []




def test_delete_nonexistent(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.delete("/api/pdfs/nonexistent")
    assert resp.status_code == 404




def test_delete_version(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "delv.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("delv.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    ver_id = versions[0]["id"]
    resp = client.delete(f"/api/versions/{ver_id}")
    assert resp.status_code == 200
    assert client.get(f"/api/pdfs/{pdf_id}/versions").json() == []




def test_delete_version_nonexistent(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.delete("/api/versions/nonexistent")
    assert resp.status_code == 404




def test_full_upload_compress_download_flow(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "flow.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("flow.pdf", f, "application/pdf")})
    assert upload.status_code == 200
    pdf_id = upload.json()["id"]
    original_size = upload.json()["file_size"]

    compress = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50", "pdf_mode": "raster"})
    assert compress.status_code == 200
    ver_id = compress.json()["id"]
    assert compress.json()["compression_ratio"] is not None

    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    assert len(versions) >= 2

    pdfs = client.get("/api/pdfs").json()
    assert len(pdfs) == 1
    assert pdfs[0]["version_count"] >= 2
    assert pdfs[0]["best_compressed_size"] <= original_size

    dl = client.get(f"/api/versions/{ver_id}/download")
    assert dl.status_code == 200
    assert len(dl.content) > 0


# ── Missing file edge cases ──



def test_sanitize_label_strips_control_chars():
    from file_compressor.web import _sanitize_label

    assert _sanitize_label("hello/world") == "hello-world"
    assert _sanitize_label("a\\b/c") == "a-b-c"
    assert _sanitize_label('a:b"c<d>e?f*g|h') == "a-b-c-d-e-f-g-h"
    assert _sanitize_label("label\x00\x1f") == "label"
    assert _sanitize_label("--already--") == "already"
    assert _sanitize_label("  trimmed  ") == "trimmed"




def test_sanitize_label_truncates_long():
    from file_compressor.web import _sanitize_label

    long = "x" * 600
    assert len(_sanitize_label(long)) <= 500


# ── _sanitize_error ──


def test_sanitize_error_no_output():
    from file_compressor.web import _sanitize_error

    assert "no output" in _sanitize_error(RuntimeError("Compression produced no output"))


def test_sanitize_error_corrupt():
    from file_compressor.web import _sanitize_error

    assert "corrupted" in _sanitize_error(RuntimeError("File is corrupt"))


def test_sanitize_error_invalid():
    from file_compressor.web import _sanitize_error

    assert "corrupted" in _sanitize_error(RuntimeError("Invalid PDF structure"))


def test_sanitize_error_strips_paths():
    from file_compressor.web import _sanitize_error

    result = _sanitize_error(RuntimeError("failed to open /Users/alice/docs/file.pdf"))
    assert "/Users" not in result
    assert result == "failed to open [path]"


def test_sanitize_error_fallback():
    from file_compressor.web import _sanitize_error

    assert _sanitize_error(RuntimeError("out of memory")) == "Compression failed"


def test_sanitize_error_preserves_non_path_text():
    from file_compressor.web import _sanitize_error

    result = _sanitize_error(RuntimeError("render failed for /Users/alice/doc.pdf page 5"))
    assert "/Users" not in result
    assert result == "render failed for [path]"


def test_sanitize_error_multiline_truncated():
    from file_compressor.web import _sanitize_error

    msg = "first line\nsecond line\nthird line"
    result = _sanitize_error(RuntimeError(msg))
    assert "second line" not in result


# ── Preview endpoint error paths ──


def test_preview_invalid_pdf_type(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/preview/bad/someid/0")
    assert resp.status_code == 400
    assert "pdf_type" in resp.json()["detail"]


def test_preview_original_not_found(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/preview/original/nonexistent/0")
    assert resp.status_code == 404


def test_preview_version_not_found(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/preview/version/nonexistent/0")
    assert resp.status_code == 404


def test_preview_page_out_of_range(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "pr.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("pr.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.get(f"/api/preview/original/{pdf_id}/99")
    assert resp.status_code == 422
    assert "out of range" in resp.json()["detail"]


def test_preview_render_file_not_found(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "prf.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("prf.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    with patch("file_compressor.web.render_page", side_effect=FileNotFoundError("gone")):
        resp = client.get(f"/api/preview/original/{pdf_id}/0")
    assert resp.status_code == 404


def test_preview_render_generic_error(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "pre.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("pre.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    with patch("file_compressor.web.render_page", side_effect=RuntimeError("render boom")):
        resp = client.get(f"/api/preview/original/{pdf_id}/0")
    assert resp.status_code == 500
    assert "Preview render failed" in resp.json()["detail"]


def test_preview_version_page(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "pv.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("pv.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    ver_id = versions[0]["id"]
    resp = client.get(f"/api/preview/version/{ver_id}/0")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_preview_version_deleted_with_parent(tmp_path: Path):
    """Deleting a parent PDF cascades to its versions, so version preview returns 404."""
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "orphan.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("orphan.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    ver_id = versions[0]["id"]

    # Delete parent PDF — CASCADE deletes versions too
    client.delete(f"/api/pdfs/{pdf_id}")

    resp = client.get(f"/api/preview/version/{ver_id}/0")
    assert resp.status_code == 404


# ── Batch compress with missing file ──


def test_batch_compress_handles_missing_file(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "bm.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("bm.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]

    # Delete the stored file to simulate missing disk file
    from file_compressor.web import _get_storage
    s = _get_storage()
    stored_path = s.get_pdf_path(pdf_id)
    if stored_path:
        stored_path.unlink(missing_ok=True)

    resp = client.post("/api/pdfs/batch-compress", data={"quality": "50"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["status"] == "error"
    assert "missing" in data["results"][0]["error"].lower() or "file" in data["results"][0]["error"].lower()


# ── Version download with missing file ──


def test_download_version_file_missing(tmp_path: Path):

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "vm.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("vm.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    ver_id = versions[0]["id"]

    # Delete the version file from disk
    from file_compressor.web import _get_storage
    s = _get_storage()
    ver_path = s.get_version_path(ver_id)
    if ver_path:
        ver_path.unlink(missing_ok=True)

    resp = client.get(f"/api/versions/{ver_id}/download")
    assert resp.status_code == 404


# ── PDF download with missing file ──


def test_download_pdf_file_missing(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "dm.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("dm.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]

    # Delete the stored PDF file
    from file_compressor.web import _get_storage
    s = _get_storage()
    stored_path = s.get_pdf_path(pdf_id)
    if stored_path:
        stored_path.unlink(missing_ok=True)

    resp = client.get(f"/api/pdfs/{pdf_id}/download")
    assert resp.status_code == 404


# ── Compress with missing original file ──


def test_compress_pdf_file_missing(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "cm.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cm.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]

    # Delete the stored PDF file
    from file_compressor.web import _get_storage
    s = _get_storage()
    stored_path = s.get_pdf_path(pdf_id)
    if stored_path:
        stored_path.unlink(missing_ok=True)

    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    assert resp.status_code == 404


# ── Label validation ──


def test_compress_rejects_label_too_long(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "ll.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("ll.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"label": "x" * 501})
    assert resp.status_code == 422
    assert "label" in resp.json()["detail"].lower()


# ── Batch limits ──


def test_batch_delete_rejects_too_many_ids(tmp_path: Path):
    client = _client(tmp_path)
    ids = [f"id{i}" for i in range(1001)]
    resp = client.post("/api/pdfs/batch-delete", json={"pdf_ids": ids})
    assert resp.status_code == 422


def test_batch_compress_rejects_too_many_ids(tmp_path: Path):
    client = _client(tmp_path)
    ids = ",".join(f"id{i}" for i in range(1001))
    resp = client.post("/api/pdfs/batch-compress", data={"pdf_ids": ids})
    assert resp.status_code == 422


def test_upload_fails_when_pymupdf_missing(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "pm.pdf")
    with open(pdf_path, "rb") as f:
        with patch("file_compressor.web._fitz", side_effect=RuntimeError("no fitz")):
            resp = client.post("/api/pdfs/upload", files={"file": ("pm.pdf", f, "application/pdf")})
    assert resp.status_code == 500
    assert "PyMuPDF" in resp.json()["detail"]
