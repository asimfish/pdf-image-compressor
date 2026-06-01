from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from file_compressor.web import app, init_storage


def _make_test_pdf(path: Path, pages: int = 3) -> Path:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} content for testing.", fontsize=24)
    doc.save(path)
    doc.close()
    return path


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


def test_health(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["pdf_count"] == 0
    assert data["version_count"] == 0


def test_health_with_data(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "test.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("test.pdf", f, "application/pdf")})
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["pdf_count"] == 1


def test_upload_pdf(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "test.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("test.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "test.pdf"
    assert data["page_count"] == 3
    assert "id" in data


def test_upload_with_notes(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "notes.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("notes.pdf", f, "application/pdf")},
            data={"notes": "Important document"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["notes"] == "Important document"


def test_list_pdfs_empty(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/pdfs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_upload_returns_warning_field(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "warn.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("warn.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert "warning" in data


def test_upload_rejects_non_pdf(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/upload", files={"file": ("test.txt", b"not a pdf", "text/plain")})
    assert resp.status_code == 400


def test_upload_rejects_invalid_pdf_content(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post(
        "/api/pdfs/upload",
        files={"file": ("bad.pdf", b"not a real pdf", "application/pdf")},
    )
    assert resp.status_code == 400


def test_upload_quality_95_creates_version(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "q95.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post(
            "/api/pdfs/upload",
            files={"file": ("q95.pdf", f, "application/pdf")},
            data={"quality": "95"},
        )
    assert upload.status_code == 200
    pdf_id = upload.json()["id"]
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    assert len(versions) >= 1
    assert versions[0]["quality"] == 95


def test_list_pdfs(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "a.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("a.pdf", f, "application/pdf")})
    resp = client.get("/api/pdfs")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_pdfs_includes_version_count(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "vc.pdf")
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


def test_download_pdf(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "dl.pdf")
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


def test_list_versions(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "v.pdf")
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


def test_compress_creates_version(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "c.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("c.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(
        f"/api/pdfs/{pdf_id}/compress",
        data={"quality": "50", "pdf_mode": "raster", "label": "test version"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "test version"
    assert data["quality"] == 50


def test_compress_nonexistent(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/nonexistent/compress", data={"quality": "50"})
    assert resp.status_code == 404


def test_batch_compress(tmp_path: Path):
    client = _client(tmp_path)
    for name in ["a.pdf", "b.pdf"]:
        pdf_path = _make_test_pdf(tmp_path / name)
        with open(pdf_path, "rb") as f:
            client.post("/api/pdfs/upload", files={"file": (name, f, "application/pdf")})
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "60"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] == 2
    assert len(data["results"]) == 2
    assert all(r["status"] == "ok" for r in data["results"])


def test_batch_compress_empty_library(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "60"})
    assert resp.status_code == 404


def test_update_notes(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "n.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("n.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.put(f"/api/pdfs/{pdf_id}/notes", json={"notes": "hello world"})
    assert resp.status_code == 200
    pdfs = client.get("/api/pdfs").json()
    assert pdfs[0]["notes"] == "hello world"


def test_update_notes_accepts_max_length(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "ml.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("ml.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.put(f"/api/pdfs/{pdf_id}/notes", json={"notes": "x" * 5000})
    assert resp.status_code == 200


def test_update_notes_rejects_too_long(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "nl.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("nl.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.put(f"/api/pdfs/{pdf_id}/notes", json={"notes": "x" * 5001})
    assert resp.status_code == 422


def test_delete_pdf(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "d.pdf")
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


def test_batch_delete(tmp_path: Path):
    client = _client(tmp_path)
    ids = []
    for name in ["x.pdf", "y.pdf", "z.pdf"]:
        pdf_path = _make_test_pdf(tmp_path / name)
        with open(pdf_path, "rb") as f:
            upload = client.post("/api/pdfs/upload", files={"file": (name, f, "application/pdf")})
        ids.append(upload.json()["id"])
    resp = client.post("/api/pdfs/batch-delete", json={"pdf_ids": ids[:2]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2
    remaining = client.get("/api/pdfs").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == ids[2]


def test_download_version(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "dv.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("dv.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    assert len(versions) >= 1
    ver_id = versions[0]["id"]
    resp = client.get(f"/api/versions/{ver_id}/download")
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_delete_version(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "delv.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("delv.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    versions = client.get(f"/api/pdfs/{pdf_id}/versions").json()
    ver_id = versions[0]["id"]
    resp = client.delete(f"/api/versions/{ver_id}")
    assert resp.status_code == 200
    assert client.get(f"/api/pdfs/{pdf_id}/versions").json() == []


def test_download_version_nonexistent(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/versions/nonexistent/download")
    assert resp.status_code == 404


def test_delete_version_nonexistent(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.delete("/api/versions/nonexistent")
    assert resp.status_code == 404


def test_batch_delete_empty(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/batch-delete", json={"pdf_ids": []})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0


# ── Validation error tests ──

def test_upload_rejects_bad_quality(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "bq.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("bq.pdf", f, "application/pdf")},
            data={"quality": "0"},
        )
    assert resp.status_code == 422
    assert "quality" in resp.json()["detail"].lower()


def test_upload_rejects_bad_mode(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "bm.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("bm.pdf", f, "application/pdf")},
            data={"pdf_mode": "invalid"},
        )
    assert resp.status_code == 422
    assert "pdf_mode" in resp.json()["detail"].lower()


def test_upload_rejects_bad_dpi(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "bd.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("bd.pdf", f, "application/pdf")},
            data={"pdf_dpi": "5"},
        )
    assert resp.status_code == 422
    assert "dpi" in resp.json()["detail"].lower()


def test_compress_rejects_bad_quality(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "cbq.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbq.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "999"})
    assert resp.status_code == 422


def test_upload_rejects_bad_target_size(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "bts.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("bts.pdf", f, "application/pdf")},
            data={"target_size": "abc"},
        )
    assert resp.status_code == 422
    assert "target_size" in resp.json()["detail"].lower()


def test_compress_rejects_bad_target_size(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "cbts.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbts.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"target_size": "invalid"})
    assert resp.status_code == 422


def test_upload_rejects_quality_above_95(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "hq.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("hq.pdf", f, "application/pdf")},
            data={"quality": "96"},
        )
    assert resp.status_code == 422


def test_upload_rejects_dpi_above_300(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "hd.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("hd.pdf", f, "application/pdf")},
            data={"pdf_dpi": "301"},
        )
    assert resp.status_code == 422


def test_compress_rejects_bad_dpi(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "cbd.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbd.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"pdf_dpi": "5"})
    assert resp.status_code == 422


def test_compress_rejects_bad_mode(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "cbm.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbm.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"pdf_mode": "invalid"})
    assert resp.status_code == 422


def test_upload_accepts_boundary_quality(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "bq1.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("bq1.pdf", f, "application/pdf")},
            data={"quality": "1"},
        )
    assert resp.status_code == 200


def test_upload_accepts_boundary_dpi(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "bd36.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("bd36.pdf", f, "application/pdf")},
            data={"pdf_dpi": "36"},
        )
    assert resp.status_code == 200


def test_compress_with_relative_target_size(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = _make_test_pdf(tmp_path / "rel.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("rel.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    original_size = upload.json()["file_size"]
    half = original_size // 2
    target = f"{half}B"
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"target_size": target})
    assert resp.status_code == 200
