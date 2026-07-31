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


def test_legacy_compress_rejects_bad_compression_level(tmp_path: Path):
    client = _client(tmp_path)
    pdf_path = make_test_pdf(tmp_path / "level.pdf")
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/compress",
            files=[("files", ("level.pdf", f, "application/pdf"))],
            data={"compression_level": "5"},
        )
    assert resp.status_code == 422
    assert "compression_level" in resp.json()["detail"]


def test_public_mode_private_api_404_has_security_headers(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    with patch("file_compressor.web._PUBLIC_MODE", True):
        resp = client.get("/api/pdfs")

    assert resp.status_code == 404
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]


def test_public_mode_access_password_flow(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    with (
        patch("file_compressor.web._PUBLIC_MODE", True),
        patch("file_compressor.web._ACCESS_PASSWORD", "secret"),
        patch("file_compressor.web._ACCESS_COOKIE_VALUE", "valid-cookie"),
    ):
        locked = client.get("/")
        assert locked.status_code == 401
        assert "super_pdf" in locked.text
        assert "访问密码" in locked.text

        health = client.get("/api/health")
        assert health.status_code == 200

        wrong = client.post("/login", data={"password": "wrong"}, follow_redirects=False)
        assert wrong.status_code == 401

        unlocked = client.post("/login", data={"password": "secret"}, follow_redirects=False)
        assert unlocked.status_code == 303
        assert "super_pdf_access=" in unlocked.headers["set-cookie"]
        assert "Max-Age=2592000" in unlocked.headers["set-cookie"]

        client.cookies.set("super_pdf_access", "valid-cookie")
        opened = client.get("/")
        assert opened.status_code == 200
        assert 'value="fidelity" checked' in opened.text


def test_public_mode_config_exposes_resource_limits(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    with (
        patch("file_compressor.web._PUBLIC_MODE", True),
        patch("file_compressor.web._PUBLIC_UPLOAD_TIMEOUT_SECONDS", 90),
        patch("file_compressor.web._PUBLIC_PROCESSING_TIMEOUT_SECONDS", 300),
        patch("file_compressor.web._PUBLIC_DOWNLOAD_TIMEOUT_SECONDS", 120),
        patch("file_compressor.web._PUBLIC_RATE_LIMIT_PER_MINUTE", 8),
    ):
        response = client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["upload_timeout_seconds"] == 90
    assert response.json()["processing_timeout_seconds"] == 300
    assert response.json()["download_timeout_seconds"] == 120
    assert response.json()["rate_limit_per_minute"] == 8


def test_public_mode_guards_respect_asgi_root_path(tmp_path: Path):
    import asyncio
    from unittest.mock import patch

    import httpx2 as httpx

    import file_compressor.web as web

    init_storage(tmp_path)

    async def request():
        transport = httpx.ASGITransport(app=web.app, root_path="/papersqueeze")
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            private_response = await client.get("/papersqueeze/api/pdfs")
            upload_response = await client.post(
                "/papersqueeze/compress",
                files=[("files", ("large.pdf", b"%PDF-1.4\n" + b"x" * 200, "application/pdf"))],
            )
            return private_response, upload_response

    with (
        patch("file_compressor.web._PUBLIC_MODE", True),
        patch("file_compressor.web._MAX_UPLOAD_BYTES", 50),
        patch("file_compressor.web._MAX_MULTIPART_OVERHEAD_BYTES", 0),
        patch("file_compressor.web._save_upload_files") as save_uploads,
    ):
        private_response, upload_response = asyncio.run(request())

    assert private_response.status_code == 404
    assert upload_response.status_code == 413
    save_uploads.assert_not_called()


def test_public_mode_rejects_oversized_request_before_parsing(tmp_path: Path):
    from unittest.mock import patch

    client = _client(tmp_path)
    body = b"%PDF-1.4\n" + b"x" * 200
    with (
        patch("file_compressor.web._PUBLIC_MODE", True),
        patch("file_compressor.web._MAX_UPLOAD_BYTES", 50),
        patch("file_compressor.web._MAX_MULTIPART_OVERHEAD_BYTES", 0),
        patch("file_compressor.web._save_upload_files") as save_uploads,
    ):
        resp = client.post(
            "/compress",
            files=[("files", ("large.pdf", body, "application/pdf"))],
        )

    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"].lower()
    assert resp.headers["x-content-type-options"] == "nosniff"
    save_uploads.assert_not_called()


def test_public_mode_busy_rejects_before_reading_body(monkeypatch):
    import asyncio
    import threading

    import httpx2 as httpx

    import file_compressor.web as web

    boundary = "papersqueeze-busy"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="busy.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "%PDF-1.4\nbusy\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    chunks_sent = 0
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    limiter = web._PublicRateLimiter(12, clock=lambda: 100.0)

    async def request():
        async def stream():
            nonlocal chunks_sent
            chunks_sent += 1
            yield body[:50]
            chunks_sent += 1
            yield body[50:]

        transport = httpx.ASGITransport(app=web.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/compress",
                content=stream(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )

    monkeypatch.setattr(web, "_PUBLIC_MODE", True)
    monkeypatch.setattr(web, "_public_rate_limiter", limiter)
    monkeypatch.setattr(web, "_public_compression_semaphore", semaphore)
    try:
        response = asyncio.run(request())
    finally:
        semaphore.release()

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert chunks_sent == 0
    assert not limiter.timestamps


def test_public_mode_rate_limit_rejects_before_reading_body(monkeypatch):
    import asyncio
    import threading

    import httpx2 as httpx

    import file_compressor.web as web

    boundary = "papersqueeze-rate-limit"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="limited.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "%PDF-1.4\nlimited\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    chunks_sent = 0
    now = [100.0]
    limiter = web._PublicRateLimiter(1, clock=lambda: now[0])
    assert limiter.allow()[0]

    async def request():
        async def stream():
            nonlocal chunks_sent
            chunks_sent += 1
            yield body

        transport = httpx.ASGITransport(app=web.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/compress",
                content=stream(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )

    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(web, "_PUBLIC_MODE", True)
    monkeypatch.setattr(web, "_public_rate_limiter", limiter)
    monkeypatch.setattr(web, "_public_compression_semaphore", semaphore)
    response = asyncio.run(request())

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert chunks_sent == 0
    assert semaphore.acquire(blocking=False)
    semaphore.release()
    now[0] = 160.0
    assert limiter.allow()[0]


def test_public_mode_upload_deadline_releases_slot(monkeypatch):
    import asyncio
    import threading

    import httpx2 as httpx

    import file_compressor.web as web

    boundary = "papersqueeze-timeout"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="slow.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "%PDF-1.4\nslow\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    chunks_sent = 0
    semaphore = threading.BoundedSemaphore(1)

    async def request():
        async def stream():
            nonlocal chunks_sent
            chunks_sent += 1
            yield body[:50]
            await asyncio.sleep(0.05)
            chunks_sent += 1
            yield body[50:]

        transport = httpx.ASGITransport(app=web.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/compress",
                content=stream(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )

    monkeypatch.setattr(web, "_PUBLIC_MODE", True)
    monkeypatch.setattr(web, "_PUBLIC_UPLOAD_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(web, "_public_compression_semaphore", semaphore)
    response = asyncio.run(request())

    assert response.status_code == 408
    # Under load the deadline may expire before chunk one, but never read chunk two.
    assert chunks_sent < 2
    assert semaphore.acquire(blocking=False)
    semaphore.release()


def test_public_mode_download_deadline_releases_slot(monkeypatch):
    import asyncio
    import threading

    import file_compressor.web as web

    semaphore = threading.BoundedSemaphore(1)
    downstream_completed = False

    async def downstream(scope, receive, send):
        nonlocal downstream_completed
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/pdf")],
            }
        )
        await asyncio.sleep(0.05)
        downstream_completed = True
        await send({"type": "http.response.body", "body": b"%PDF-1.4", "more_body": False})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/compress",
        "headers": [(b"content-length", b"0")],
    }
    monkeypatch.setattr(web, "_PUBLIC_MODE", True)
    monkeypatch.setattr(web, "_PUBLIC_DOWNLOAD_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(web, "_public_compression_semaphore", semaphore)

    asyncio.run(web.PublicUploadLimitMiddleware(downstream)(scope, receive, send))

    assert sent[0]["status"] == 200
    assert not downstream_completed
    assert semaphore.acquire(blocking=False)
    semaphore.release()


def test_cleanup_file_response_removes_temp_dir_on_cancel(tmp_path: Path):
    import asyncio

    import pytest

    import file_compressor.web as web

    temp_dir = tmp_path / "response"
    temp_dir.mkdir()
    output = temp_dir / "compressed.pdf"
    output.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    response = web.CleanupFileResponse(
        output,
        filename=output.name,
        media_type="application/pdf",
        cleanup_path=temp_dir,
    )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            await asyncio.sleep(1)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/compressed.pdf",
        "headers": [],
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 123),
        "extensions": {},
    }

    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(response(scope, receive, send), timeout=0.01)

    asyncio.run(run())

    assert not temp_dir.exists()


def test_cleanup_file_response_streams_before_pathsend_cleanup(tmp_path: Path):
    import asyncio

    import file_compressor.web as web

    temp_dir = tmp_path / "pathsend_response"
    temp_dir.mkdir()
    output = temp_dir / "compressed.pdf"
    payload = b"%PDF-1.4\n" + b"x" * 100
    output.write_bytes(payload)
    response = web.CleanupFileResponse(
        output,
        filename=output.name,
        media_type="application/pdf",
        cleanup_path=temp_dir,
    )
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/compressed.pdf",
        "headers": [],
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 123),
        "extensions": {"http.response.pathsend": {}},
    }

    asyncio.run(response(scope, receive, send))

    assert not any(message["type"] == "http.response.pathsend" for message in messages)
    assert b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    ) == payload
    assert not temp_dir.exists()


def test_public_processing_timeout_terminates_worker(monkeypatch, tmp_path: Path):
    import asyncio

    import pytest

    import file_compressor.web as web
    from file_compressor.models import CompressionConfig

    class HangingProcess:
        exitcode = None

        def __init__(self):
            self.started = False
            self.terminated = False
            self.closed = False

        def start(self):
            self.started = True

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return not self.terminated

        def terminate(self):
            self.terminated = True
            self.exitcode = -15

        def kill(self):
            self.terminated = True
            self.exitcode = -9

        def close(self):
            self.closed = True

    process = HangingProcess()

    class FakeContext:
        def Process(self, **kwargs):
            return process

    monkeypatch.setattr(web.multiprocessing, "get_context", lambda method: FakeContext())
    monkeypatch.setattr(web, "_PUBLIC_PROCESSING_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(web.HTTPException) as exc_info:
        asyncio.run(
            web._run_public_compression(
                tmp_path / "source.pdf",
                CompressionConfig(output_dir=tmp_path),
                tmp_path / "output.pdf",
            )
        )

    assert exc_info.value.status_code == 504
    assert process.started
    assert process.terminated
    assert process.closed


def test_public_processing_cancellation_terminates_worker(monkeypatch, tmp_path: Path):
    import asyncio
    import threading

    import pytest

    import file_compressor.web as web
    from file_compressor.models import CompressionConfig

    class HangingProcess:
        exitcode = None

        def __init__(self):
            self.terminated = False
            self.closed = False

        def start(self):
            return None

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return not self.terminated

        def terminate(self):
            self.terminated = True
            self.exitcode = -15

        def kill(self):
            self.terminated = True
            self.exitcode = -9

        def close(self):
            self.closed = True

    process = HangingProcess()
    cleanup_started = threading.Event()
    cleanup_release = threading.Event()
    original_cleanup = web._cleanup_public_process

    class FakeContext:
        def Process(self, **kwargs):
            return process

    def blocking_cleanup(worker):
        cleanup_started.set()
        cleanup_release.wait(1)
        original_cleanup(worker)

    async def run_and_cancel():
        task = asyncio.create_task(
            web._run_public_compression(
                tmp_path / "source.pdf",
                CompressionConfig(output_dir=tmp_path),
                tmp_path / "output.pdf",
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        while not cleanup_started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(web.multiprocessing, "get_context", lambda method: FakeContext())
    monkeypatch.setattr(web, "_cleanup_public_process", blocking_cleanup)
    try:
        asyncio.run(run_and_cancel())
    finally:
        cleanup_release.set()

    assert process.terminated
    assert process.closed


def test_deferred_cancellation_logs_inner_error(caplog):
    import asyncio
    import logging

    import pytest

    import file_compressor.web as web

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def fail_after_release():
            started.set()
            await release.wait()
            raise RuntimeError("inner failure")

        inner = asyncio.create_task(fail_after_release())
        waiter = asyncio.create_task(web._await_task_completion(inner))
        await started.wait()
        waiter.cancel()
        await asyncio.sleep(0)
        release.set()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        else:
            pytest.fail("Expected deferred cancellation")

    with caplog.at_level(logging.WARNING, logger="pdf_manager"):
        asyncio.run(scenario())

    assert "Operation failed while cancellation was pending: inner failure" in caplog.text


def test_public_middleware_double_cancel_releases_slot_after_cleanup(monkeypatch):
    import asyncio
    import threading

    import pytest

    import file_compressor.web as web

    semaphore = threading.BoundedSemaphore(1)
    downstream_started = threading.Event()
    cleanup_started = threading.Event()
    cleanup_release = threading.Event()

    def blocking_cleanup():
        cleanup_started.set()
        cleanup_release.wait(1)

    async def downstream(scope, receive, send):
        downstream_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await web._run_thread_complete(blocking_cleanup)

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        return None

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/compress",
        "headers": [(b"content-length", b"0")],
    }

    async def scenario():
        task = asyncio.create_task(
            web.PublicUploadLimitMiddleware(downstream)(scope, receive, send)
        )
        while not downstream_started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        while not cleanup_started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not semaphore.acquire(blocking=False)
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(web, "_PUBLIC_MODE", True)
    monkeypatch.setattr(web, "_public_rate_limiter", None)
    monkeypatch.setattr(web, "_public_compression_semaphore", semaphore)
    try:
        asyncio.run(scenario())
    finally:
        cleanup_release.set()

    assert semaphore.acquire(blocking=False)
    semaphore.release()


def test_public_route_cancellation_removes_temp_dir(monkeypatch, tmp_path: Path):
    import asyncio
    from io import BytesIO
    from unittest.mock import AsyncMock

    import pytest
    from fastapi import UploadFile

    import file_compressor.web as web
    from file_compressor.models import CompressionConfig

    temp_dir = tmp_path / "cancelled-request"
    upload = UploadFile(
        filename="cancel.pdf",
        file=BytesIO(make_test_pdf(tmp_path / "source.pdf").read_bytes()),
    )
    monkeypatch.setattr(web, "_PUBLIC_MODE", True)
    monkeypatch.setattr(web.tempfile, "mkdtemp", lambda prefix: str(temp_dir))
    monkeypatch.setattr(web, "_validate_public_pdf", lambda path: None)
    monkeypatch.setattr(
        web,
        "_run_public_compression",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            web.compress_upload(
                files=[upload],
                config=CompressionConfig(),
                max_edge=None,
                to_webp=False,
                archive=None,
            )
        )

    assert not temp_dir.exists()


def test_public_mode_limits_chunked_body_before_multipart_parsing(monkeypatch):
    import asyncio
    from unittest.mock import patch

    import httpx2 as httpx

    import file_compressor.web as web

    boundary = "papersqueeze-test"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="large.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + b"%PDF-1.4\n" + b"x" * 200 + f"\r\n--{boundary}--\r\n".encode()
    captured_headers = {}
    chunks_sent = 0

    async def capture_app(scope, receive, send):
        captured_headers.update(dict(scope["headers"]))
        await web.app(scope, receive, send)

    async def request():
        async def stream():
            nonlocal chunks_sent
            chunks_sent += 1
            yield body[:100]
            chunks_sent += 1
            yield body[100:]

        transport = httpx.ASGITransport(app=capture_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/compress",
                content=stream(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )

    monkeypatch.setattr(web, "_PUBLIC_MODE", True)
    monkeypatch.setattr(web, "_MAX_UPLOAD_BYTES", 5)
    monkeypatch.setattr(web, "_MAX_MULTIPART_OVERHEAD_BYTES", 0)
    with patch("file_compressor.web._save_upload_files") as save_uploads:
        response = asyncio.run(request())

    assert b"content-length" not in captured_headers
    assert captured_headers.get(b"transfer-encoding") == b"chunked"
    assert chunks_sent == 1
    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"
    save_uploads.assert_not_called()


def test_public_mode_releases_compression_slot_after_failure(tmp_path: Path):
    import threading
    from unittest.mock import patch

    client = _client(tmp_path)
    pdf_data = make_test_pdf(tmp_path / "failure.pdf").read_bytes()
    semaphore = threading.BoundedSemaphore(1)
    with (
        patch("file_compressor.web._PUBLIC_MODE", True),
        patch("file_compressor.web._public_compression_semaphore", semaphore),
        patch("file_compressor.web._run_public_compression", side_effect=RuntimeError("boom")),
    ):
        first = client.post(
            "/compress",
            files=[("files", ("failure.pdf", pdf_data, "application/pdf"))],
        )
        second = client.post(
            "/compress",
            files=[("files", ("failure.pdf", pdf_data, "application/pdf"))],
        )

    assert first.status_code == 500
    assert second.status_code == 500




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

