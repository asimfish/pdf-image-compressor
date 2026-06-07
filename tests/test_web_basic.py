from pathlib import Path

from fastapi.testclient import TestClient

from file_compressor.web import app, init_storage

from conftest import make_test_pdf


def _client(tmp_path: Path) -> TestClient:
    init_storage(tmp_path)
    return TestClient(app)

def test_index_returns_html(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "PDF Manager" in resp.text




def test_stats_empty(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pdf_count"] == 0
    assert data["version_count"] == 0




def test_stats_with_data(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "st.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("st.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pdf_count"] == 1
    assert data["version_count"] >= 2
    assert data["total_original_bytes"] > 0
    assert data["total_compressed_bytes"] > 0
    assert data["total_saved_bytes"] >= 0




def test_health(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["pdf_count"] == 0
    assert data["version_count"] == 0




def test_health_with_versions(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "hv.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("hv.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pdf_count"] == 1
    assert data["version_count"] >= 2




def test_health_with_data(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "test.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("test.pdf", f, "application/pdf")})
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["pdf_count"] == 1




def test_list_pdfs_empty(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/pdfs")
    assert resp.status_code == 200
    assert resp.json() == []




def test_list_pdfs(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "a.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("a.pdf", f, "application/pdf")})
    resp = client.get("/api/pdfs")
    assert resp.status_code == 200
    assert len(resp.json()) == 1




def test_list_pdfs_includes_version_count(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "vc.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("vc.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]

    pdfs = client.get("/api/pdfs").json()
    assert pdfs[0]["version_count"] >= 1
    assert pdfs[0]["best_compression_ratio"] is not None
    assert pdfs[0]["best_compressed_size"] is not None

    client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    pdfs = client.get("/api/pdfs").json()
    assert pdfs[0]["version_count"] >= 2




def test_list_pdfs_no_duplicates_with_same_size_versions(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "dup.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("dup.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    # Compress twice with identical settings to produce same file size
    client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50", "pdf_mode": "optimize"})
    client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50", "pdf_mode": "optimize"})
    pdfs = client.get("/api/pdfs").json()
    assert len(pdfs) == 1
    assert pdfs[0]["version_count"] >= 3




def test_list_versions(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "v.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post(
            "/api/pdfs/upload",
            files={"file": ("v.pdf", f, "application/pdf")},
            data={"target_size": "50KB"},
        )
    pdf_id = upload.json()["id"]
    resp = client.get(f"/api/pdfs/{pdf_id}/versions")
    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) >= 1




def test_list_versions_nonexistent_pdf(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/pdfs/nonexistent/versions")
    assert resp.status_code == 404




def test_version_list_includes_strip_metadata(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "vsm.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("vsm.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50", "strip_metadata": "false"})
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    assert len(versions) >= 1
    # Find the version with strip_metadata=false
    sm_false = [v for v in versions if v["strip_metadata"] is False]
    assert len(sm_false) >= 1




def test_get_storage_lazy_init(tmp_path: Path):
    from unittest.mock import patch
    import file_compressor.web as web_module

    saved = web_module._storage
    try:
        web_module._storage = None
        with patch("pathlib.Path.home", return_value=tmp_path):
            storage = web_module._get_storage()
            assert storage is not None
            assert (tmp_path / ".pdf-manager" / "library.db").exists()
    finally:
        web_module._storage = saved


