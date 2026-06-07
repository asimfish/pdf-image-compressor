from pathlib import Path

from fastapi.testclient import TestClient

from file_compressor.web import app, init_storage

from conftest import make_test_pdf


def _client(tmp_path: Path) -> TestClient:
    init_storage(tmp_path)
    return TestClient(app)

def test_download_pdf(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "dl.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("dl.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.get(f"/api/pdfs/{pdf_id}/download")
    assert resp.status_code == 200
    assert len(resp.content) > 0




def test_download_nonexistent(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/pdfs/nonexistent/download")
    assert resp.status_code == 404




def test_download_version(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "dv.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("dv.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    assert len(versions) >= 1
    ver_id = versions[0]["id"]
    resp = client.get(f"/api/versions/{ver_id}/download")
    assert resp.status_code == 200
    assert len(resp.content) > 0
    cd = resp.headers.get("content-disposition", "")
    assert "dv" in cd  # original filename stem in download name




def test_download_version_filename_with_label(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "report.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("report.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50", "label": "Email version"})
    ver_id = resp.json()["id"]
    dl = client.get(f"/api/versions/{ver_id}/download")
    assert dl.status_code == 200
    cd = dl.headers.get("content-disposition", "")
    assert "report_Email" in cd




def test_download_version_nonexistent(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/versions/nonexistent/download")
    assert resp.status_code == 404




def test_download_pdf_file_missing_on_disk(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "miss.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("miss.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    # Delete the original file from storage
    from file_compressor.web import _get_storage
    storage = _get_storage()
    stored_path = storage.get_pdf_path(pdf_id)
    stored_path.unlink()
    resp = client.get(f"/api/pdfs/{pdf_id}/download")
    assert resp.status_code == 404




def test_download_version_file_missing(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "missv.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("missv.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    ver_id = versions[0]["id"]

    # Delete the version file from disk
    from file_compressor.web import _get_storage
    storage = _get_storage()
    vpath = storage.get_version_path(ver_id)
    vpath.unlink()

    resp = client.get(f"/api/versions/{ver_id}/download")
    assert resp.status_code == 404


# ── strip_metadata tests ──



def test_download_version_label_sanitized(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "rpt.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("rpt.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50", "label": "v1/beta\\test"})
    ver_id = resp.json()["id"]
    dl = client.get(f"/api/versions/{ver_id}/download")
    assert dl.status_code == 200
    cd = dl.headers.get("content-disposition", "")
    assert "v1-beta-test" in cd




def test_preview_original_page(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "preview.pdf", pages=3)
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("preview.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]

    resp = client.get(f"/api/preview/original/{pdf_id}/0")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:4] == b"\x89PNG"




def test_preview_original_second_page(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "preview2.pdf", pages=3)
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("preview2.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]

    resp = client.get(f"/api/preview/original/{pdf_id}/2")
    assert resp.status_code == 200
    assert len(resp.content) > 0




def test_preview_original_out_of_range(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "preview_oob.pdf", pages=2)
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("preview_oob.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]

    resp = client.get(f"/api/preview/original/{pdf_id}/5")
    assert resp.status_code == 422




def test_preview_version_page(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "preview_v.pdf", pages=2)
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("preview_v.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]

    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "60", "pdf_mode": "raster", "pdf_dpi": "80"})
    assert resp.status_code == 200
    version_id = resp.json()["id"]

    resp = client.get(f"/api/preview/version/{version_id}/0")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:4] == b"\x89PNG"




def test_preview_invalid_type(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "preview_bad.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("preview_bad.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]

    resp = client.get(f"/api/preview/invalid/{pdf_id}/0")
    assert resp.status_code == 400




def test_preview_original_not_found(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/preview/original/nonexistent/0")
    assert resp.status_code == 404




def test_preview_version_not_found(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/preview/version/nonexistent/0")
    assert resp.status_code == 404


def test_preview_negative_page(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "neg.pdf", pages=3)
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("neg.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]

    resp = client.get(f"/api/preview/original/{pdf_id}/-1")
    assert resp.status_code == 422


