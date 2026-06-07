from pathlib import Path

from fastapi.testclient import TestClient

from file_compressor.web import app, init_storage

from conftest import make_test_pdf


def _client(tmp_path: Path) -> TestClient:
    init_storage(tmp_path)
    return TestClient(app)

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



def test_legacy_compress_rejects_invalid_archive(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "test.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/compress", files={"files": ("test.pdf", f, "application/pdf")}, data={"archive": "tar"})
    assert resp.status_code == 422
    assert "archive" in resp.json()["detail"].lower()




def test_legacy_compress_rejects_bad_max_edge(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "test.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/compress", files={"files": ("test.pdf", f, "application/pdf")}, data={"max_edge": "50"})
    assert resp.status_code == 422
    assert "max_edge" in resp.json()["detail"].lower()




def test_legacy_compress_accepts_valid_archive_zip(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "test.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/compress", files={"files": ("test.pdf", f, "application/pdf")}, data={"archive": "zip"})
    assert resp.status_code == 200


def test_legacy_compress_deduplicates_filenames(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "dup.pdf")
    data = pdf_path.read_bytes()
    resp = client.post(
        "/compress",
        files=[
            ("files", ("dup.pdf", data, "application/pdf")),
            ("files", ("dup.pdf", data, "application/pdf")),
        ],
        data={"archive": "zip"},
    )
    assert resp.status_code == 200

