from pathlib import Path

from fastapi.testclient import TestClient

from file_compressor.web import app, init_storage

from conftest import make_test_pdf


def _client(tmp_path: Path) -> TestClient:
    init_storage(tmp_path)
    return TestClient(app)

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




def test_upload_returns_warning_field(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "warn.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("warn.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert "warning" in data


def test_upload_returns_compression_info(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "comp.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("comp.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert "best_compressed_size" in data
    assert "best_compression_ratio" in data
    assert data["best_compressed_size"] > 0
    assert isinstance(data["best_compression_ratio"], (int, float))




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


