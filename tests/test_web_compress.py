from pathlib import Path

from fastapi.testclient import TestClient

from file_compressor.web import app, init_storage

from conftest import make_test_pdf


def _client(tmp_path: Path) -> TestClient:
    init_storage(tmp_path)
    return TestClient(app)

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


def test_compress_missing_file(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "test.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("test.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]
    # Delete the original file from disk
    originals = tmp_path / "originals"
    for f in originals.iterdir():
        f.unlink()
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    assert resp.status_code == 404
    assert "missing" in resp.json()["detail"].lower()



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
    not_found = [r for r in data["results"] if r["status"] == "not_found"]
    assert len(not_found) == 2
    assert {r["pdf_id"] for r in not_found} == {"nonexistent1", "nonexistent2"}




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




def test_batch_delete_empty(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post("/api/pdfs/batch-delete", json={"pdf_ids": []})
    assert resp.status_code == 422


# ── Validation error tests ──



def test_compress_rejects_bad_quality(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "cbq.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbq.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "999"})
    assert resp.status_code == 422




def test_compress_rejects_bad_target_size(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "cbts.pdf")
    with open(pdf_path, "rb") as f:
        upload = client.post("/api/pdfs/upload", files={"file": ("cbts.pdf", f, "application/pdf")})
    pdf_id = upload.json()["id"]
    resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"target_size": "invalid"})
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




def test_batch_compress_handles_missing_file(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "test.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("test.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]
    # Delete the original file from disk to simulate missing file
    originals = tmp_path / "originals"
    for f in originals.iterdir():
        f.unlink()
    resp = client.post("/api/pdfs/batch-compress", data={"quality": "60"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["compressed"] == 0
    assert len(data["results"]) == 1
    assert data["results"][0]["status"] == "error"
    assert "missing" in data["results"][0]["error"].lower()


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


def test_batch_compress_does_not_leak_exception_details(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "secret.pdf")
    with open(pdf_path, "rb") as f:
        client.post("/api/pdfs/upload", files={"file": ("secret.pdf", f, "application/pdf")})

    with patch("file_compressor.web._compress_and_store", side_effect=RuntimeError("/internal/path/secret")):
        resp = client.post("/api/pdfs/batch-compress", data={"quality": "50"})
    assert resp.status_code == 200
    errors = [r for r in resp.json()["results"] if r["status"] == "error"]
    assert len(errors) == 1
    assert "/internal/path" not in errors[0]["error"]
    assert "secret" not in errors[0]["error"]


def test_compress_error_message_no_output(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "noout.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("noout.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]

    with patch("file_compressor.web._compress_and_store", side_effect=RuntimeError("no output produced")):
        resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    assert resp.status_code == 500
    assert "corrupted" in resp.json()["detail"].lower()


def test_compress_error_message_corrupt(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "corrupt.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("corrupt.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]

    with patch("file_compressor.web._compress_and_store", side_effect=RuntimeError("invalid xref table")):
        resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    assert resp.status_code == 500
    assert "corrupted" in resp.json()["detail"].lower() or "invalid" in resp.json()["detail"].lower()


def test_compress_error_message_strips_paths(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "patherr.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post("/api/pdfs/upload", files={"file": ("patherr.pdf", f, "application/pdf")})
    pdf_id = resp.json()["id"]

    with patch("file_compressor.web._compress_and_store", side_effect=RuntimeError("disk full at /var/data/lib")):
        resp = client.post(f"/api/pdfs/{pdf_id}/compress", data={"quality": "50"})
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "/var/data" not in detail


