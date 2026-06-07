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


