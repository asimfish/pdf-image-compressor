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


def test_upload_pdf(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "test.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("test.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "test.pdf"
    assert data["page_count"] == 3
    assert "id" in data


def test_upload_with_notes(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "notes.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("notes.pdf", f, "application/pdf")},
            data={"notes": "Important document"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["notes"] == "Important document"


def test_upload_rejects_notes_too_long(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "longnotes.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("longnotes.pdf", f, "application/pdf")},
            data={"notes": "x" * 5001},
        )
    assert resp.status_code == 422


def test_list_pdfs_empty(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/pdfs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_upload_returns_warning_field(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "warn.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("warn.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert "warning" in data


def test_upload_warns_on_compression_failure(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "fail.pdf")
    with patch("file_compressor.web.compress_path", side_effect=RuntimeError("boom")):
        with open(pdf_path, "rb") as f:
            resp = client.post("/api/pdfs/upload", files={"file": ("fail.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["warning"] is not None
    assert "compression failed" in data["warning"].lower()


def test_compress_and_store_raises_on_no_output(tmp_path: Path):
    from unittest.mock import patch
    from file_compressor.models import CompressionResult, CompressionSummary

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "nope.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("nope.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]

    fake_summary = CompressionSummary(results=[
        CompressionResult(source=pdf_path, output=None, original_size=100, compressed_size=None, status="failed", error="kaboom")
    ])
    with patch("file_compressor.web.compress_path", return_value=fake_summary):
        resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Compression failed"


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


def test_upload_rejects_zero_page_pdf(tmp_path: Path):
    client = _client(tmp_path)
    zero_page_pdf = (
        b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
        b"xref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000058 00000 n \ntrailer<</Size 3/Root 1 0 R>>\n"
        b"startxref\n109\n%%EOF"
    )
    resp = client.post(
        "/api/pdfs/upload",
        files={"file": ("empty.pdf", zero_page_pdf, "application/pdf")},
    )
    assert resp.status_code == 400
    assert "no pages" in resp.json()["detail"].lower()


def test_upload_quality_95_creates_version(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "q95.pdf")
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


def test_compress_creates_version(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "c.pdf")
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


def test_compress_with_grayscale(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "gr.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("gr.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(
        f"/api/pdfs/{pdf_id}/compress",
        data={"pdf_mode": "raster", "pdf_grayscale": "true"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pdf_grayscale"] is True


def test_compress_nonexistent(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/nonexistent/compress", data={"quality": "50"})
    assert resp.status_code == 404


def test_batch_compress(tmp_path: Path):
    client = _client(tmp_path)
    for name in ["a.pdf", "b.pdf"]:
        pdf_path = make_test_pdf(tmp_path / name)
        with open(pdf_path, "rb") as f:
            client.post("/api/pdfs/upload", files={"file": (name, f, "application/pdf")})
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "60"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] == 2
    assert len(data["results"]) == 2
    assert all(r["status"] == "ok" for r in data["results"])


def test_batch_compress_with_pdf_ids(tmp_path: Path):
    client = _client(tmp_path)
    ids = []
    for name in ["a.pdf", "b.pdf"]:
        pdf_path = make_test_pdf(tmp_path / name)
        with open(pdf_path, "rb") as f:
            resp = client.post("/api/pdfs/upload", files={"file": (name, f, "application/pdf")})
        ids.append(resp.json()["id"])
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "60", "pdf_ids": ids[0]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] == 1
    assert len(data["results"]) == 1
    assert data["results"][0]["pdf_id"] == ids[0]


def test_batch_compress_empty_library(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "60"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] == 0
    assert data["results"] == []


def test_batch_compress_with_nonexistent_pdf_ids(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "a.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("a.pdf", f, "application/pdf")})
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "60", "pdf_ids": "nonexistent1,nonexistent2"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] == 0
    assert data["results"] == []


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


def test_upload_multiple_files(tmp_path: Path):
    client = _client(tmp_path)
    make_test_pdf(tmp_path / "a.pdf")
    make_test_pdf(tmp_path / "b.pdf")
    with open(tmp_path / "a.pdf", "rb") as fa, open(tmp_path / "b.pdf", "rb") as fb:
        resp_a = client.post("/api/pdfs/upload", files={"file": ("a.pdf", fa, "application/pdf")})
        resp_b = client.post("/api/pdfs/upload", files={"file": ("b.pdf", fb, "application/pdf")})
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    pdfs = client.get("/api/pdfs").json()
    assert len(pdfs) == 2


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


def test_batch_delete(tmp_path: Path):
    client = _client(tmp_path)
    ids = []
    for name in ["x.pdf", "y.pdf", "z.pdf"]:
        pdf_path = make_test_pdf(tmp_path / name)
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


def test_list_versions_nonexistent_pdf(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.get("/api/pdfs/nonexistent/versions")
    assert resp.status_code == 404


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
    pdf_path = make_test_pdf(tmp_path / "bq.pdf")
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
    pdf_path = make_test_pdf(tmp_path / "bm.pdf")
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
    pdf_path = make_test_pdf(tmp_path / "bd.pdf")
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
    pdf_path = make_test_pdf(tmp_path / "cbq.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbq.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "999"})
    assert resp.status_code == 422


def test_upload_rejects_bad_target_size(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "bts.pdf")
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
    pdf_path = make_test_pdf(tmp_path / "cbts.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbts.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"target_size": "invalid"})
    assert resp.status_code == 422


def test_upload_rejects_quality_above_95(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "hq.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("hq.pdf", f, "application/pdf")},
            data={"quality": "96"},
        )
    assert resp.status_code == 422


def test_upload_rejects_dpi_above_300(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "hd.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("hd.pdf", f, "application/pdf")},
            data={"pdf_dpi": "301"},
        )
    assert resp.status_code == 422


def test_compress_rejects_bad_dpi(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "cbd.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbd.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"pdf_dpi": "5"})
    assert resp.status_code == 422


def test_compress_rejects_bad_mode(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "cbm.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbm.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"pdf_mode": "invalid"})
    assert resp.status_code == 422


def test_compress_rejects_label_too_long(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "lbl.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("lbl.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"label": "x" * 501})
    assert resp.status_code == 422
    assert "label" in resp.json()["detail"].lower()


def test_upload_accepts_boundary_quality(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "bq1.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("bq1.pdf", f, "application/pdf")},
            data={"quality": "1"},
        )
    assert resp.status_code == 200


def test_upload_accepts_boundary_dpi(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "bd36.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("bd36.pdf", f, "application/pdf")},
            data={"pdf_dpi": "36"},
        )
    assert resp.status_code == 200


def test_compress_with_relative_target_size(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "rel.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("rel.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    original_size = upload.json()["file_size"]
    half = original_size // 2
    target = f"{half}B"
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"target_size": target})
    assert resp.status_code == 200


def test_compress_with_target_size_generates_label(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "al.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("al.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"target_size": "50KB", "quality": "60", "pdf_mode": "raster"})
    assert resp.status_code == 200
    data = resp.json()
    assert "50" in data["label"]
    assert "Q60" in data["label"]
    assert "raster" in data["label"]


def test_compress_auto_label_no_target(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "nl2.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("nl2.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "70", "pdf_mode": "optimize"})
    assert resp.status_code == 200
    data = resp.json()
    assert "Q70" in data["label"]
    assert "optimize" in data["label"]


def test_compress_auto_label_with_target(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "nl3.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("nl3.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "82", "target_size": "500KB"})
    assert resp.status_code == 200
    data = resp.json()
    assert "500" in data["label"]
    assert "Q82" in data["label"]


def test_upload_with_target_size(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "ts.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("ts.pdf", f, "application/pdf")},
            data={"target_size": "100KB"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "ts.pdf"


def test_compress_minimal_quality(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "mq.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("mq.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "1", "pdf_mode": "raster"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["quality"] == 1


def test_batch_compress_rejects_invalid_params(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "0"})
    assert resp.status_code == 422


def test_batch_compress_rejects_bad_mode(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/batch-compress", data={"pdf_mode": "invalid"})
    assert resp.status_code == 422


def test_batch_compress_rejects_bad_dpi(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/batch-compress", data={"pdf_dpi": "5"})
    assert resp.status_code == 422


def test_batch_compress_rejects_bad_target_size(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/batch-compress", data={"target_size": "abc"})
    assert resp.status_code == 422


def test_upload_rejects_file_too_large(tmp_path: Path):
    client = _client(tmp_path)
    from unittest.mock import patch
    # Create a fake large file by mocking the size check
    big_data = b"%PDF-1.4 fake" + b"\x00" * 100
    with patch("file_compressor.web._MAX_UPLOAD_BYTES", 50):
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("big.pdf", big_data, "application/pdf")},
        )
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"].lower()


# ── Legacy /compress endpoint tests ──

def test_legacy_compress_single_file(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "legacy.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/compress",
            files=[("files", ("legacy.pdf", f, "application/pdf"))],
            data={"quality": "70"},
        )
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_legacy_compress_with_grayscale(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "gray.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/compress",
            files=[("files", ("gray.pdf", f, "application/pdf"))],
            data={"pdf_grayscale": "true"},
        )
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_legacy_compress_rejects_non_pdf(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post(
        "/compress",
        files=[("files", ("test.txt", b"not a pdf", "text/plain"))],
    )
    assert resp.status_code == 500


def test_legacy_compress_with_target_size(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "ts.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/compress",
            files=[("files", ("ts.pdf", f, "application/pdf"))],
            data={"target_size": "500KB"},
        )
    assert resp.status_code == 200


def test_legacy_compress_with_pdf_mode(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "mode.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/compress",
            files=[("files", ("mode.pdf", f, "application/pdf"))],
            data={"pdf_mode": "raster", "pdf_dpi": "150"},
        )
    assert resp.status_code == 200


def test_legacy_compress_handles_compression_failure(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "fail.pdf")
    with patch("file_compressor.web.compress_path", side_effect=RuntimeError("legacy boom")):
        with open(pdf_path, "rb") as f:
            resp = client.post("/compress", files=[("files", ("fail.pdf", f, "application/pdf"))])
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Compression failed"


def test_legacy_compress_rejects_bad_quality(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "lq.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/compress", files=[("files", ("lq.pdf", f, "application/pdf"))], data={"quality": "0"})
    assert resp.status_code == 422


def test_legacy_compress_rejects_bad_mode(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "lm.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/compress", files=[("files", ("lm.pdf", f, "application/pdf"))], data={"pdf_mode": "invalid"})
    assert resp.status_code == 422


def test_legacy_compress_rejects_bad_target_size(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "lt.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/compress", files=[("files", ("lt.pdf", f, "application/pdf"))], data={"target_size": "abc"})
    assert resp.status_code == 422


def test_legacy_compress_strip_metadata_false(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "lsm.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/compress",
            files=[("files", ("lsm.pdf", f, "application/pdf"))],
            data={"strip_metadata": "false"},
        )
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_legacy_compress_rejects_file_too_large(tmp_path: Path):
    client = _client(tmp_path)
    from unittest.mock import patch

    big_data = b"%PDF-1.4 fake" + b"\x00" * 200
    with patch("file_compressor.web._MAX_UPLOAD_BYTES", 50):
        resp = client.post(
            "/compress",
            files=[("files", ("big.pdf", big_data, "application/pdf"))],
            data={"quality": "70"},
        )
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"].lower()


# ── Integration test ──

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


def test_compress_pdf_original_missing(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "miss2.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("miss2.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    from file_compressor.web import _get_storage
    storage = _get_storage()
    stored_path = storage.get_pdf_path(pdf_id)
    stored_path.unlink()
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    assert resp.status_code == 404


def test_batch_compress_handles_individual_errors(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "ok.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("ok.pdf", f, "application/pdf")})
    # Upload a second PDF then delete its file to cause compression error
    pdf_path2 = make_test_pdf(tmp_path / "bad.pdf")
    with open(pdf_path2, "rb") as f:
        upload2 = client.post("/api/pdfs/upload", files={"file": ("bad.pdf", f, "application/pdf")})
    bad_id = upload2.json()["id"]
    from file_compressor.web import _get_storage
    storage = _get_storage()
    storage.get_pdf_path(bad_id).unlink()

    resp = client.post("/api/pdfs/batch-compress", data={"quality": "50"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] >= 0
    errors = [r for r in data["results"] if r["status"] == "error"]
    assert any("missing" in e["error"].lower() for e in errors)


def test_batch_compress_reports_compression_errors(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "fail.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("fail.pdf", f, "application/pdf")})

    with patch("file_compressor.web.compress_path", side_effect=RuntimeError("compression exploded")):
        resp = client.post("/api/pdfs/batch-compress", data={"quality": "50"})
    assert resp.status_code == 200
    data = resp.json()
    errors = [r for r in data["results"] if r["status"] == "error"]
    assert any(e["error"] == "Compression failed" for e in errors)


def test_batch_compress_with_target_size(tmp_path: Path):
    client = _client(tmp_path)
    for name in ["a.pdf", "b.pdf"]:
        pdf_path = make_test_pdf(tmp_path / name)
        with open(pdf_path, "rb") as f:
            client.post("/api/pdfs/upload", files={"file": (name, f, "application/pdf")})
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "50", "target_size": "50KB"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] == 2
    assert all(r["status"] == "ok" for r in data["results"])


def test_batch_compress_with_raster_mode(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "r.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("r.pdf", f, "application/pdf")})
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "40", "pdf_mode": "raster", "pdf_dpi": "80"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] == 1
    assert data["results"][0]["status"] == "ok"


def test_batch_compress_with_grayscale(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "g.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("g.pdf", f, "application/pdf")})
    resp = client.post("/api/pdfs/batch-compress", data={"pdf_mode": "raster", "pdf_grayscale": "true"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] == 1


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

def test_upload_strip_metadata_true(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "sm.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("sm.pdf", f, "application/pdf")},
            data={"strip_metadata": "true"},
        )
    assert resp.status_code == 200


def test_upload_strip_metadata_false(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "smf.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/api/pdfs/upload",
            files={"file": ("smf.pdf", f, "application/pdf")},
            data={"strip_metadata": "false"},
        )
    assert resp.status_code == 200


def test_compress_strip_metadata_false(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "csm.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("csm.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(
        f"/api/pdfs/{pdf_id}/compress",
        data={"quality": "50", "strip_metadata": "false"},
    )
    assert resp.status_code == 200


def test_compress_strip_metadata_true(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "csmt.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("csmt.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(
        f"/api/pdfs/{pdf_id}/compress",
        data={"quality": "50", "strip_metadata": "true"},
    )
    assert resp.status_code == 200


def test_batch_compress_strip_metadata_false(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "bsm.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("bsm.pdf", f, "application/pdf")})
    resp = client.post(
        "/api/pdfs/batch-compress",
        data={"quality": "50", "strip_metadata": "false"},
    )
    assert resp.status_code == 200
    assert resp.json()["compressed"] == 1


def test_batch_compress_with_label(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "bl.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("bl.pdf", f, "application/pdf")})
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "50", "label": "Batch v1"})
    assert resp.status_code == 200
    assert resp.json()["compressed"] == 1


def test_batch_compress_includes_savings(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "sav.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("sav.pdf", f, "application/pdf")})
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "50"})
    assert resp.status_code == 200
    data = resp.json()
    ok = data["results"][0]
    assert ok["status"] == "ok"
    assert "original_size" in ok
    assert "compressed_size" in ok
    assert "compression_ratio" in ok
    assert ok["original_size"] > 0


def test_batch_compress_rejects_long_label(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/batch-compress", data={"label": "x" * 501})
    assert resp.status_code == 422


def test_compress_passes_strip_metadata_to_config(tmp_path: Path):
    from unittest.mock import patch
    from file_compressor.models import CompressionConfig

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "cfg.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cfg.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]

    captured_configs = []
    original_compress_path = None

    def mock_compress(src, config, output=None):
        captured_configs.append(config)
        from file_compressor.core import compress_path as real_compress
        return real_compress(src, config, output)

    with patch("file_compressor.web.compress_path", side_effect=mock_compress):
        resp = client.post(
            f"/api/pdfs/{pdf_id}/compress",
            data={"quality": "50", "strip_metadata": "false"},
        )
    assert resp.status_code == 200
    assert len(captured_configs) == 1
    assert captured_configs[0].strip_metadata is False


def test_compress_strip_metadata_default_true(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "def.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("def.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]

    captured_configs = []

    def mock_compress(src, config, output=None):
        captured_configs.append(config)
        from file_compressor.core import compress_path as real_compress
        return real_compress(src, config, output)

    with patch("file_compressor.web.compress_path", side_effect=mock_compress):
        resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    assert resp.status_code == 200
    assert len(captured_configs) == 1
    assert captured_configs[0].strip_metadata is True


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


def test_compress_500_does_not_leak_details(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "secret.pdf")
    with patch("file_compressor.web._compress_and_store", side_effect=RuntimeError("/internal/path/secret")):
        with open(pdf_path, "rb") as f:
            resp = client.post("/api/pdfs/upload", files={"file": ("secret.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]
    with patch("file_compressor.web._compress_and_store", side_effect=RuntimeError("/internal/path/secret")):
        resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "/internal/path" not in detail
    assert "secret" not in detail
