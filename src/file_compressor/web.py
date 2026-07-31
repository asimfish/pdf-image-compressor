from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import multiprocessing
import os
import re
import shutil
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .core import compress_path
from .models import CompressionConfig
from .pdfs import _fitz, evict_render_cache, render_page
from .public_worker import compress_pdf_to_output
from .storage import Storage, VersionParams, VersionRecord
from .utils import clamp_quality, format_size, parse_size, unique_path

logger = logging.getLogger("pdf_manager")

_STATIC = Path(__file__).parent / "static"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


_PUBLIC_MODE = os.getenv("PDF_COMPRESSOR_PUBLIC_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
_MAX_UPLOAD_BYTES = _env_int(
    "PDF_COMPRESSOR_MAX_UPLOAD_MB",
    30 if _PUBLIC_MODE else 500,
    1,
    500,
) * 1024 * 1024
_MAX_MULTIPART_OVERHEAD_BYTES = 64 * 1024
_MAX_PUBLIC_PAGES = _env_int("PDF_COMPRESSOR_MAX_PAGES", 100, 1, 2000)
_PUBLIC_UPLOAD_TIMEOUT_SECONDS = _env_int(
    "PDF_COMPRESSOR_UPLOAD_TIMEOUT_SECONDS", 120, 10, 900
)
_PUBLIC_DOWNLOAD_TIMEOUT_SECONDS = _env_int(
    "PDF_COMPRESSOR_DOWNLOAD_TIMEOUT_SECONDS", 120, 10, 900
)
_PUBLIC_PROCESSING_TIMEOUT_SECONDS = _env_int(
    "PDF_COMPRESSOR_PROCESSING_TIMEOUT_SECONDS", 300, 30, 1800
)
_PUBLIC_RATE_LIMIT_PER_MINUTE = _env_int(
    "PDF_COMPRESSOR_RATE_LIMIT_PER_MINUTE", 12, 1, 120
)
_PUBLIC_COMPRESSION_SLOTS = _env_int("PDF_COMPRESSOR_CONCURRENCY", 1, 1, 4)
_public_compression_semaphore = threading.BoundedSemaphore(_PUBLIC_COMPRESSION_SLOTS)

_ACCESS_PASSWORD = os.getenv("PDF_COMPRESSOR_ACCESS_PASSWORD", "")
_ACCESS_COOKIE_NAME = "super_pdf_access"
_ACCESS_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
_ACCESS_COOKIE_VALUE = (
    hmac.new(
        _ACCESS_PASSWORD.encode("utf-8"),
        b"super-pdf-access-v1",
        hashlib.sha256,
    ).hexdigest()
    if _ACCESS_PASSWORD
    else ""
)


def _access_cookie_valid(request: Request) -> bool:
    if not _ACCESS_PASSWORD:
        return True
    value = request.cookies.get(_ACCESS_COOKIE_NAME, "")
    return hmac.compare_digest(value, _ACCESS_COOKIE_VALUE)


def _access_login_html(error: str = "") -> str:
    error_html = f'<p class="error">{error}</p>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>super_pdf · 访问验证</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f6f3ea; color: #13231d; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    form {{ width: min(420px, calc(100% - 32px)); padding: 32px; border: 1px solid #d9dfd8; border-radius: 24px; background: rgba(255,255,255,.9); box-shadow: 0 24px 80px rgba(35,56,47,.12); }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: -.03em; }}
    p {{ color: #607068; line-height: 1.6; }}
    input {{ width: 100%; box-sizing: border-box; margin: 18px 0 14px; padding: 13px 14px; border: 1px solid #d9dfd8; border-radius: 14px; font: inherit; }}
    button {{ width: 100%; padding: 13px 14px; border: 0; border-radius: 14px; color: white; background: #126b4f; font: inherit; font-weight: 700; cursor: pointer; }}
    .error {{ margin: 0 0 10px; color: #b42318; }}
  </style>
</head>
<body>
  <form method="post" action="/login">
    <h1>super_pdf</h1>
    <p>首次访问需要输入访问密码。验证成功后，本浏览器 30 天内不用再输入。</p>
    {error_html}
    <input name="password" type="password" autocomplete="current-password" placeholder="访问密码" required autofocus>
    <button type="submit">进入</button>
  </form>
</body>
</html>"""


def _scope_route_path(scope: Scope) -> str:
    path = scope.get("path", "")
    root_path = scope.get("root_path", "").rstrip("/")
    if not root_path:
        return path
    if path == root_path:
        return "/"
    if path.startswith(root_path + "/"):
        return path[len(root_path) :]
    return path


class _PublicRateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.clock = clock
        self.timestamps: deque[float] = deque()
        self.lock = threading.Lock()

    def allow(self) -> tuple[bool, int]:
        with self.lock:
            now = self.clock()
            cutoff = now - 60
            while self.timestamps and self.timestamps[0] <= cutoff:
                self.timestamps.popleft()
            if len(self.timestamps) >= self.requests_per_minute:
                retry_after = max(
                    1,
                    min(60, math.ceil(60 - (now - self.timestamps[0]))),
                )
                return False, retry_after
            self.timestamps.append(now)
        return True, 0


_public_rate_limiter = (
    _PublicRateLimiter(_PUBLIC_RATE_LIMIT_PER_MINUTE) if _PUBLIC_MODE else None
)


class CleanupFileResponse(FileResponse):
    """Stream a temporary file, then remove its directory even if cancelled."""

    def __init__(self, *args, cleanup_path: Path, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cleanup_path = cleanup_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        stream_scope = scope
        if "http.response.pathsend" in scope.get("extensions", {}):
            # Starlette returns after pathsend, before the server has consumed the
            # file. Force body streaming so cleanup cannot race the file transfer.
            stream_scope = dict(scope)
            extensions = dict(scope["extensions"])
            extensions.pop("http.response.pathsend")
            stream_scope["extensions"] = extensions
        try:
            await super().__call__(stream_scope, receive, send)
        finally:
            await _run_thread_complete(shutil.rmtree, self.cleanup_path, True)


class PublicUploadLimitMiddleware:
    """Gate public jobs and stop oversized uploads while the body is streaming."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        is_public_upload = (
            _PUBLIC_MODE
            and scope["type"] == "http"
            and scope.get("method") == "POST"
            and _scope_route_path(scope) in {"/compress", "/compress/"}
        )
        if not is_public_upload:
            await self.app(scope, receive, send)
            return

        semaphore = _public_compression_semaphore
        acquired_slot = semaphore.acquire(blocking=False)
        if not acquired_slot:
            await JSONResponse(
                status_code=429,
                content={"detail": "The compressor is busy; please retry shortly"},
                headers={"Retry-After": "1"},
            )(scope, receive, send)
            return

        limiter = _public_rate_limiter
        if limiter is not None:
            allowed, retry_after = limiter.allow()
            if not allowed:
                semaphore.release()
                await JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded; please retry later"},
                    headers={"Retry-After": str(retry_after)},
                )(scope, receive, send)
                return

        response_started = asyncio.Event()

        async def track_response_start(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_started.set()
            await send(message)

        job = asyncio.create_task(
            self._handle_public_upload(scope, receive, track_response_start)
        )
        response_waiter = asyncio.create_task(response_started.wait())
        try:
            completed, _ = await asyncio.wait(
                {job, response_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if job in completed:
                await job
            else:
                try:
                    await asyncio.wait_for(job, timeout=_PUBLIC_DOWNLOAD_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    logger.warning("Public download exceeded the response deadline")
        finally:
            if not job.done():
                job.cancel()
            if not response_waiter.done():
                response_waiter.cancel()
            cleanup = asyncio.ensure_future(
                asyncio.gather(job, response_waiter, return_exceptions=True)
            )
            try:
                await _await_task_completion(cleanup)
            finally:
                semaphore.release()

    async def _handle_public_upload(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = _MAX_UPLOAD_BYTES + _MAX_MULTIPART_OVERHEAD_BYTES
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                request_bytes = int(content_length)
            except ValueError:
                await JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})(
                    scope, receive, send
                )
                return
            if request_bytes < 0:
                await JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})(
                    scope, receive, send
                )
                return
            if request_bytes > limit:
                await JSONResponse(status_code=413, content={"detail": "Upload too large"})(
                    scope, receive, send
                )
                return

        deadline = asyncio.get_running_loop().time() + _PUBLIC_UPLOAD_TIMEOUT_SECONDS
        received = 0
        abort_response: Optional[JSONResponse] = None
        replacement_sent = False

        async def limited_receive() -> Message:
            nonlocal abort_response, received
            if abort_response is not None:
                return {"type": "http.disconnect"}
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                abort_response = JSONResponse(
                    status_code=408,
                    content={"detail": "Upload timed out"},
                )
                return {"type": "http.disconnect"}
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError:
                abort_response = JSONResponse(
                    status_code=408,
                    content={"detail": "Upload timed out"},
                )
                return {"type": "http.disconnect"}
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    abort_response = JSONResponse(
                        status_code=413,
                        content={"detail": "Upload too large"},
                    )
                    return {"type": "http.disconnect"}
            return message

        async def send_replacement() -> None:
            nonlocal replacement_sent
            if replacement_sent or abort_response is None:
                return
            replacement_sent = True
            await abort_response(scope, receive, send)

        async def limited_send(message: Message) -> None:
            if abort_response is not None:
                await send_replacement()
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            if abort_response is None:
                raise
        if abort_response is not None:
            await send_replacement()


app = FastAPI(
    title="PDF Manager",
    docs_url=None if _PUBLIC_MODE else "/docs",
    redoc_url=None if _PUBLIC_MODE else "/redoc",
    openapi_url=None if _PUBLIC_MODE else "/openapi.json",
)
app.add_middleware(PublicUploadLimitMiddleware)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

_VALID_MODES = {"auto", "fidelity", "optimize", "raster", "text"}
_MAX_NOTES_LEN = 5000
_MAX_LABEL_LEN = 500

_PRIVATE_API_PREFIXES = (
    "/api/pdfs",
    "/api/versions",
    "/api/preview",
    "/api/stats",
)


def _apply_security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if _PUBLIC_MODE:
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
    return response


@app.middleware("http")
async def public_mode_guard(request: Request, call_next):
    """Keep the shared library private when running the anonymous public site."""
    route_path = _scope_route_path(request.scope)
    if _PUBLIC_MODE and _ACCESS_PASSWORD:
        if route_path == "/login":
            if request.method == "POST":
                form = await request.form()
                password = str(form.get("password", ""))
                if hmac.compare_digest(password, _ACCESS_PASSWORD):
                    response = RedirectResponse("/", status_code=303)
                    response.set_cookie(
                        _ACCESS_COOKIE_NAME,
                        _ACCESS_COOKIE_VALUE,
                        max_age=_ACCESS_COOKIE_MAX_AGE,
                        httponly=True,
                        samesite="lax",
                        secure=request.url.scheme == "https",
                    )
                    return _apply_security_headers(response)
                return _apply_security_headers(
                    HTMLResponse(
                        _access_login_html("密码不正确，请重试。"),
                        status_code=401,
                    )
                )
            return _apply_security_headers(HTMLResponse(_access_login_html()))
        if route_path == "/api/health" or route_path.startswith("/static/"):
            return await call_next(request)
        if not _access_cookie_valid(request):
            return _apply_security_headers(
                HTMLResponse(_access_login_html(), status_code=401)
            )
    if _PUBLIC_MODE and any(
        route_path == prefix or route_path.startswith(prefix + "/")
        for prefix in _PRIVATE_API_PREFIXES
    ):
        return _apply_security_headers(
            JSONResponse(status_code=404, content={"detail": "Not available in public mode"})
        )
    response = await call_next(request)
    return _apply_security_headers(response)


def _validate_compress_params(
    quality: int,
    pdf_mode: str,
    pdf_dpi: int,
    target_size: Optional[str] = None,
    compression_level: int = 2,
) -> None:
    if clamp_quality(quality) != quality:
        raise HTTPException(422, detail="quality must be between 1 and 95")
    if pdf_mode not in _VALID_MODES:
        raise HTTPException(422, detail=f"pdf_mode must be one of {sorted(_VALID_MODES)}")
    if not 36 <= pdf_dpi <= 300:
        raise HTTPException(422, detail="pdf_dpi must be between 36 and 300")
    if not 1 <= compression_level <= 4:
        raise HTTPException(422, detail="compression_level must be between 1 and 4")
    if target_size:
        try:
            parse_size(target_size)
        except ValueError:
            raise HTTPException(422, detail="Invalid target_size format")


def _compress_form(
    quality: int = Form(82),
    target_size: Optional[str] = Form(None),
    pdf_mode: str = Form("fidelity"),
    pdf_dpi: int = Form(120),
    pdf_grayscale: bool = Form(False),
    strip_metadata: bool = Form(True),
    compression_level: int = Form(2),
) -> CompressionConfig:
    _validate_compress_params(quality, pdf_mode, pdf_dpi, target_size, compression_level)
    return CompressionConfig(
        quality=quality, target_bytes=parse_size(target_size), pdf_mode=pdf_mode,
        pdf_dpi=pdf_dpi, pdf_grayscale=pdf_grayscale, strip_metadata=strip_metadata,
        compression_level=compression_level,
    )


_storage: Optional[Storage] = None
_storage_lock = threading.Lock()


def _get_storage() -> Storage:
    global _storage
    if _storage is None:
        with _storage_lock:
            if _storage is None:
                data_dir = Path.home() / ".pdf-manager"
                _storage = Storage(data_dir)
                removed = _storage.cleanup_orphans()
                if removed:
                    logger.info("Cleaned up %d orphaned file(s)", removed)
    return _storage


def _sanitize_label(label: str) -> str:
    """Sanitize a label for use in download filenames."""
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", label)
    label = re.sub(r"-{2,}", "-", label).strip("- ")
    return label[:_MAX_LABEL_LEN] or "version"


_PATH_RE = re.compile(r"(/[^/\s]+(?:\s+[^/\s]+)*)+|([A-Za-z]:[/\\][^\s]+)")


def _sanitize_error(exc: Exception) -> str:
    """Return a user-friendly error message without leaking internal details."""
    msg = str(exc).split("\n")[0][:200]
    if isinstance(exc, FileNotFoundError):
        return "The file is no longer available on disk"
    if isinstance(exc, PermissionError):
        return "Insufficient permissions to access the file"
    if isinstance(exc, OSError):
        return "File access error"
    if "no output" in msg.lower():
        return "Compression produced no output — the file may be corrupted"
    if "corrupt" in msg.lower() or "invalid" in msg.lower():
        return "The file appears to be corrupted or invalid"
    sanitized = _PATH_RE.sub("[path]", msg)
    if sanitized != msg:
        # Path was present — return sanitized message without internal paths
        return sanitized if sanitized else "Compression failed"
    return "Compression failed"


def init_storage(data_dir: Path) -> None:
    global _storage
    with _storage_lock:
        if _storage is not None:
            _storage.close()
        _storage = Storage(data_dir)


class NotesUpdate(BaseModel):
    notes: str = ""

    @field_validator("notes")
    @classmethod
    def notes_not_too_long(cls, v: str) -> str:
        v = v.strip()
        if len(v) > _MAX_NOTES_LEN:
            raise ValueError(f"notes must be {_MAX_NOTES_LEN} characters or fewer")
        return v


# ── Frontend ──

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = "public.html" if _PUBLIC_MODE else "index.html"
    html = (_STATIC / page).read_text(encoding="utf-8")
    return HTMLResponse(content=html)


# ── Health ──

@app.get("/api/health")
def api_health() -> dict:
    if _PUBLIC_MODE:
        return {
            "status": "ok",
            "mode": "public",
            "compression_slots": _PUBLIC_COMPRESSION_SLOTS,
            "rate_limit_per_minute": _PUBLIC_RATE_LIMIT_PER_MINUTE,
        }
    storage = _get_storage()
    stats = storage.stats()
    return {"status": "ok", "pdf_count": stats["pdf_count"], "version_count": stats["version_count"]}


# ── Stats ──

@app.get("/api/stats")
def api_stats() -> dict:
    return _get_storage().stats()


@app.get("/api/config")
def api_config() -> dict:
    return {
        "max_upload_bytes": _MAX_UPLOAD_BYTES,
        "max_pages": _MAX_PUBLIC_PAGES if _PUBLIC_MODE else None,
        "upload_timeout_seconds": _PUBLIC_UPLOAD_TIMEOUT_SECONDS if _PUBLIC_MODE else None,
        "processing_timeout_seconds": (
            _PUBLIC_PROCESSING_TIMEOUT_SECONDS if _PUBLIC_MODE else None
        ),
        "download_timeout_seconds": _PUBLIC_DOWNLOAD_TIMEOUT_SECONDS if _PUBLIC_MODE else None,
        "rate_limit_per_minute": _PUBLIC_RATE_LIMIT_PER_MINUTE if _PUBLIC_MODE else None,
        "public_mode": _PUBLIC_MODE,
    }


# ── PDF CRUD ──

@app.get("/api/pdfs")
def api_list_pdfs() -> list:
    return [asdict(p) for p in _get_storage().list_pdfs_with_stats()]


@app.post("/api/pdfs/upload")
def api_upload_pdf(
    file: UploadFile = File(...),
    config: CompressionConfig = Depends(_compress_form),
    notes: str = Form(""),
):
    notes = notes.strip()
    if len(notes) > _MAX_NOTES_LEN:
        raise HTTPException(422, detail=f"notes must be {_MAX_NOTES_LEN} characters or fewer")
    storage = _get_storage()
    filename = Path(file.filename or "upload.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, detail="Only PDF files are supported")

    data = _read_upload_with_limit(file)
    try:
        fitz = _fitz()
    except RuntimeError:
        raise HTTPException(500, detail="PDF processing unavailable (PyMuPDF not installed)")
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            page_count = len(doc)
    except Exception:
        raise HTTPException(400, detail="Invalid PDF file")
    if page_count == 0:
        raise HTTPException(400, detail="PDF has no pages")

    pdf = storage.add_pdf(filename, data, page_count, notes, group_existing=True)
    logger.info("Uploaded PDF %s (%s, %d pages) [id=%s]", filename, format_size(len(data)), page_count, pdf.id)
    result = asdict(pdf)
    result["warning"] = None

    try:
        stored_path = storage.get_pdf_path(pdf.id)
        ver = _compress_and_store(storage, pdf.id, stored_path, config, label="Initial compression")
        result["best_compressed_size"] = ver.file_size
        result["best_compression_ratio"] = ver.compression_ratio
    except Exception as exc:
        logger.warning("Initial compression failed for %s: %s", filename, exc)
        result["warning"] = "Upload succeeded but initial compression failed"

    return result


@app.get("/api/pdfs/{pdf_id}/download")
def api_download_pdf(pdf_id: str) -> FileResponse:
    storage = _get_storage()
    pdf = storage.get_pdf(pdf_id)
    if not pdf:
        raise HTTPException(404, detail="PDF not found")
    path = storage.get_pdf_path(pdf_id)
    if not path or not path.exists():
        raise HTTPException(404, detail="Original file missing")
    try:
        return FileResponse(path, filename=_sanitize_label(pdf.filename), media_type="application/pdf")
    except FileNotFoundError:
        raise HTTPException(404, detail="Original file missing")


@app.get("/api/pdfs/{pdf_id}/versions")
def api_list_versions(pdf_id: str) -> list:
    storage = _get_storage()
    if not storage.get_pdf(pdf_id):
        raise HTTPException(404, detail="PDF not found")
    return [asdict(v) for v in storage.list_versions(pdf_id)]


@app.post("/api/pdfs/{pdf_id}/compress")
def api_compress_pdf(
    pdf_id: str,
    config: CompressionConfig = Depends(_compress_form),
    label: str = Form(""),
):
    if len(label) > _MAX_LABEL_LEN:
        raise HTTPException(422, detail=f"label must be {_MAX_LABEL_LEN} characters or fewer")
    storage = _get_storage()
    pdf = storage.get_pdf(pdf_id)
    if not pdf:
        raise HTTPException(404, detail="PDF not found")
    path = storage.get_pdf_path(pdf_id)
    if not path:
        raise HTTPException(404, detail="Original file missing")
    if not path.exists():
        raise HTTPException(404, detail="Original file missing")
    if not label:
        label = _auto_label(config.target_bytes, config.quality, config.pdf_mode)

    try:
        ver = _compress_and_store(storage, pdf_id, path, config, label)
    except Exception as exc:
        logger.error("Compression failed for pdf %s: %s", pdf_id, exc)
        raise HTTPException(500, detail=_sanitize_error(exc))
    logger.info("Compressed pdf %s: %s → %s (%.1f%% reduction)", pdf_id, format_size(pdf.file_size), format_size(ver.file_size), (ver.compression_ratio or 0) * 100)
    result = asdict(ver)
    if config.target_bytes and ver.file_size > config.target_bytes:
        result["warning"] = f"Target {format_size(config.target_bytes)} not achievable — smallest result is {format_size(ver.file_size)}"
    return result


@app.post("/api/pdfs/batch-compress")
def api_batch_compress(
    config: CompressionConfig = Depends(_compress_form),
    pdf_ids: Optional[str] = Form(None),
    label: str = Form(""),
):
    if len(label) > _MAX_LABEL_LEN:
        raise HTTPException(422, detail=f"label must be {_MAX_LABEL_LEN} characters or fewer")
    storage = _get_storage()
    if pdf_ids is not None:
        id_list = list(dict.fromkeys(s.strip() for s in pdf_ids.split(",") if s.strip()))
        if not id_list:
            raise HTTPException(422, detail="pdf_ids is empty")
        if len(id_list) > 50:
            raise HTTPException(422, detail="Too many PDF IDs (max 50)")
        pdfs = storage.get_pdfs_by_ids(id_list)
        found_ids = {p.id for p in pdfs}
        not_found = [{"pdf_id": pid, "filename": pid[:8] + "…", "status": "not_found", "error": "PDF not found (may have been deleted)"} for pid in id_list if pid not in found_ids]
    else:
        pdfs = storage.list_pdfs()
        if len(pdfs) > 50:
            raise HTTPException(422, detail="Too many PDFs (max 50)")
        not_found = []
    if not pdfs and not not_found:
        return {"compressed": 0, "results": []}

    if not label:
        label = _auto_label(config.target_bytes, config.quality, config.pdf_mode)
    logger.info("Batch compress: %d PDFs, quality=%d, mode=%s", len(pdfs), config.quality, config.pdf_mode)
    results = []
    for pdf in pdfs:
        try:
            path = storage.get_pdf_path(pdf.id)
            if not path or not path.exists():
                results.append({"pdf_id": pdf.id, "filename": pdf.filename, "status": "error", "error": "File missing from disk"})
                continue
            ver = _compress_and_store(storage, pdf.id, path, config, label)
            results.append({"pdf_id": pdf.id, "filename": pdf.filename, "version_id": ver.id, "status": "ok",
                            "original_size": pdf.file_size, "compressed_size": ver.file_size, "compression_ratio": ver.compression_ratio})
        except Exception as exc:
            logger.warning("Batch compress failed for %s: %s", pdf.id, exc)
            results.append({"pdf_id": pdf.id, "filename": pdf.filename, "status": "error", "error": _sanitize_error(exc)})

    return {"compressed": len([r for r in results if r["status"] == "ok"]), "results": results + not_found}


@app.put("/api/pdfs/{pdf_id}/notes")
def api_update_notes(pdf_id: str, body: NotesUpdate) -> dict:
    storage = _get_storage()
    if not storage.update_notes(pdf_id, body.notes):
        raise HTTPException(404, detail="PDF not found")
    return {"ok": True}


@app.delete("/api/pdfs/{pdf_id}")
def api_delete_pdf(pdf_id: str) -> dict:
    storage = _get_storage()
    paths = [p for v in storage.list_versions(pdf_id) if (p := storage.get_version_path(v.id))]
    pdf_path = storage.get_pdf_path(pdf_id)
    if pdf_path:
        paths.append(pdf_path)
    if not storage.delete_pdf(pdf_id):
        raise HTTPException(404, detail="PDF not found")
    for p in paths:
        evict_render_cache(p)
    logger.info("Deleted PDF %s", pdf_id)
    return {"ok": True}


class BatchDeleteRequest(BaseModel):
    pdf_ids: list[str] = Field(..., min_length=1, max_length=50)


@app.post("/api/pdfs/batch-delete")
def api_batch_delete(body: BatchDeleteRequest) -> dict:
    storage = _get_storage()
    evict_paths: list[Path] = []
    for pid in body.pdf_ids:
        for v in storage.list_versions(pid):
            vp = storage.get_version_path(v.id)
            if vp:
                evict_paths.append(vp)
        pp = storage.get_pdf_path(pid)
        if pp:
            evict_paths.append(pp)
    deleted = storage.batch_delete_pdfs(body.pdf_ids)
    for p in evict_paths:
        evict_render_cache(p)
    return {"deleted": deleted}


# ── Version CRUD ──

@app.get("/api/versions/{version_id}/download")
def api_download_version(version_id: str) -> FileResponse:
    storage = _get_storage()
    ver = storage.get_version(version_id)
    if not ver:
        raise HTTPException(404, detail="Version not found")
    path = storage.get_version_path(version_id)
    if not path or not path.exists():
        raise HTTPException(404, detail="Version file missing")
    pdf = storage.get_pdf(ver.pdf_id)
    stem = _sanitize_label(Path(pdf.filename).stem) if pdf else "version"
    label = _sanitize_label(ver.label) if ver.label else ""
    download_name = f"{stem}_{label}.pdf" if label else f"{stem}.pdf"
    if len(download_name.encode("utf-8")) > 200:
        if label:
            stem_b = stem.encode("utf-8")[:150].decode("utf-8", errors="replace")
            label_b = label.encode("utf-8")[:40].decode("utf-8", errors="replace")
            download_name = f"{stem_b}_{label_b}.pdf"
        else:
            stem_b = stem.encode("utf-8")[:190].decode("utf-8", errors="replace")
            download_name = stem_b + ".pdf"
    try:
        return FileResponse(path, filename=download_name, media_type="application/pdf")
    except FileNotFoundError:
        raise HTTPException(404, detail="Version file missing")


@app.delete("/api/versions/{version_id}")
def api_delete_version(version_id: str) -> dict:
    storage = _get_storage()
    ver = storage.get_version(version_id)
    if ver is None:
        raise HTTPException(404, detail="Version not found")
    ver_path = storage.get_version_path(version_id)
    if ver_path:
        evict_render_cache(ver_path)
    if not storage.delete_version(version_id):
        raise HTTPException(404, detail="Version not found")
    return {"ok": True}


@app.get("/api/preview/{pdf_type}/{item_id}/{page}")
def api_preview_page(pdf_type: str, item_id: str, page: int) -> StreamingResponse:
    storage = _get_storage()
    if pdf_type == "original":
        pdf = storage.get_pdf(item_id)
        if not pdf:
            raise HTTPException(404, detail="PDF not found")
        path = storage.get_pdf_path(item_id)
        if not path:
            raise HTTPException(404, detail="Original file missing")
        total = pdf.page_count
    elif pdf_type == "version":
        ver = storage.get_version(item_id)
        if not ver:
            raise HTTPException(404, detail="Version not found")
        path = storage.get_version_path(item_id)
        if not path:
            raise HTTPException(404, detail="Version file missing")
        pdf = storage.get_pdf(ver.pdf_id)
        if not pdf:
            raise HTTPException(404, detail="Parent PDF not found")
        total = ver.page_count if ver.page_count > 0 else pdf.page_count
    else:
        raise HTTPException(400, detail="pdf_type must be 'original' or 'version'")
    if not path:
        raise HTTPException(404, detail="Original file missing")
    if page < 0:
        raise HTTPException(422, detail=f"Page {page} out of range")
    if total is not None and total == 0:
        label = "this PDF" if pdf_type == "original" else "this version"
        raise HTTPException(422, detail=f"Cannot preview {label}: no readable pages")
    if total is not None and total > 0 and page >= total:
        raise HTTPException(422, detail=f"Page {page} out of range (0-{total - 1})")
    try:
        png_data = render_page(path, page)
    except FileNotFoundError:
        raise HTTPException(404, detail="Original file missing" if pdf_type == "original" else "Version file missing")
    except ValueError:
        label = "this PDF" if pdf_type == "original" else "this version"
        raise HTTPException(422, detail=f"Page {page} not available in {label}")
    except Exception as exc:
        logger.error("Preview render failed for %s/%s page %d: %s", pdf_type, item_id, page, _sanitize_error(exc))
        raise HTTPException(500, detail="Preview render failed")

    return StreamingResponse(BytesIO(png_data), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


_COPY_CHUNK = 1024 * 1024  # 1 MB


def _read_upload_with_limit(file: UploadFile) -> bytes:
    """Read an upload file in chunks, enforcing size limit before full allocation."""
    buf = bytearray()
    while True:
        chunk = file.file.read(_COPY_CHUNK)
        if not chunk:
            break
        if len(buf) + len(chunk) > _MAX_UPLOAD_BYTES:
            raise HTTPException(413, detail=f"File too large ({format_size(len(buf) + len(chunk))}). Maximum is {format_size(_MAX_UPLOAD_BYTES)}.")
        buf.extend(chunk)
    return bytes(buf)


def _save_upload_files(files: list[UploadFile], dest: Path) -> None:
    for item in files:
        safe_name = Path(item.filename or "uploaded.bin").name
        target = unique_path(dest / safe_name, overwrite=False)
        written = 0
        with target.open("wb") as handle:
            while True:
                chunk = item.file.read(_COPY_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES:
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, detail=f"File too large ({safe_name}, {format_size(written)}). Maximum is {format_size(_MAX_UPLOAD_BYTES)}.")
                handle.write(chunk)


def _validate_public_pdf(path: Path) -> None:
    """Validate anonymous uploads before starting an expensive compression job."""
    try:
        fitz = _fitz()
        with fitz.open(path) as doc:
            if doc.needs_pass:
                raise HTTPException(400, detail="Password-protected PDFs are not supported")
            page_count = len(doc)
            if page_count == 0:
                raise HTTPException(400, detail="PDF has no pages")
            if page_count > _MAX_PUBLIC_PAGES:
                raise HTTPException(
                    413,
                    detail=f"PDF has {page_count} pages; public limit is {_MAX_PUBLIC_PAGES}",
                )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, detail="Invalid PDF file")


def _cleanup_public_process(process: Any) -> None:
    if process.is_alive():
        process.terminate()
        process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)
    if process.is_alive():
        logger.error("Compression worker could not be stopped")
        return
    try:
        process.close()
    except ValueError:
        logger.warning("Compression worker could not be closed")


async def _await_task_completion(task: asyncio.Future[Any]) -> Any:
    cancellation: Optional[asyncio.CancelledError] = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except BaseException:
            # A completed task's exception is handled by task.result() below.
            pass
    try:
        result = task.result()
    except BaseException as exc:
        if cancellation is not None:
            logger.warning(
                "Operation failed while cancellation was pending: %s",
                exc,
                exc_info=True,
            )
            raise cancellation from exc
        raise
    if cancellation is not None:
        raise cancellation
    return result


async def _run_thread_complete(function: Callable[..., Any], *args: Any) -> Any:
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    return await _await_task_completion(task)


async def _run_public_compression(
    source: Path,
    config: CompressionConfig,
    output: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=compress_pdf_to_output,
        args=(str(source), config, str(output)),
        name="papersqueeze-compression",
    )
    try:
        await _run_thread_complete(process.start)
    except BaseException:
        await _run_thread_complete(_cleanup_public_process, process)
        raise

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _PUBLIC_PROCESSING_TIMEOUT_SECONDS
    try:
        while process.is_alive():
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise HTTPException(504, detail="Compression timed out")
            await asyncio.sleep(min(0.1, remaining))
        if process.exitcode != 0 or not output.is_file():
            raise RuntimeError("Compression worker failed")
    finally:
        await _run_thread_complete(_cleanup_public_process, process)


# ── Legacy compress endpoint (backward compat) ──

@app.post("/compress")
async def compress_upload(
    files: list[UploadFile] = File(...),
    config: CompressionConfig = Depends(_compress_form),
    max_edge: Optional[int] = Form(None),
    to_webp: bool = Form(False),
    archive: Optional[str] = Form(None),
) -> FileResponse:
    if archive is not None and archive != "zip":
        raise HTTPException(422, detail="archive must be None or 'zip'")
    if max_edge is not None and (max_edge < 100 or max_edge > 10000):
        raise HTTPException(422, detail="max_edge must be between 100 and 10000")
    if _PUBLIC_MODE:
        if len(files) != 1:
            raise HTTPException(422, detail="Public mode accepts exactly one PDF at a time")
        if archive is not None:
            raise HTTPException(422, detail="Archives are not available in public mode")
        filename = Path(files[0].filename or "upload.pdf").name
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(400, detail="Only PDF files are supported")

    temp = tempfile.mkdtemp(prefix="file_compressor_web_")
    temp_path = Path(temp)
    upload_dir = temp_path / "uploads"
    output_dir = temp_path / "compressed"
    upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        await _run_thread_complete(_save_upload_files, files, upload_dir)
        config = replace(config, max_edge=max_edge, to_webp=to_webp, output_dir=output_dir, archive=archive)
        if len(files) > 1 or archive == "zip":
            source = upload_dir
        else:
            source = next(upload_dir.iterdir(), None)
            if source is None:
                raise HTTPException(400, detail="No files uploaded")
        if _PUBLIC_MODE:
            await _run_thread_complete(_validate_public_pdf, source)
            result_path = output_dir / "compressed.pdf"
            await _run_public_compression(source, config, result_path)
        else:
            output = output_dir / "compressed.zip" if archive == "zip" else None
            summary = await _run_thread_complete(compress_path, source, config, output)
            result = summary.archive or next(
                (item for item in summary.results if item.output is not None),
                None,
            )
            if result is None or result.output is None:
                raise HTTPException(500, detail="Compression produced no output")
            result_path = result.output
    except HTTPException:
        await _run_thread_complete(shutil.rmtree, temp_path, True)
        raise
    except asyncio.CancelledError:
        await _run_thread_complete(shutil.rmtree, temp_path, True)
        raise
    except Exception as exc:
        await _run_thread_complete(shutil.rmtree, temp_path, True)
        logger.error("Legacy compress failed: %s", _sanitize_error(exc))
        raise HTTPException(500, detail="Compression failed")
    media_type = "application/zip" if result_path.suffix.lower() == ".zip" else "application/pdf"
    response = CleanupFileResponse(
        result_path,
        filename=result_path.name,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
        cleanup_path=temp_path,
    )
    return response


# ── Helpers ──


def _compress_and_store(
    storage: Storage,
    pdf_id: str,
    source_path: Path,
    config: CompressionConfig,
    label: str,
) -> VersionRecord:
    with tempfile.TemporaryDirectory(prefix="pdf_compress_") as td:
        td_path = Path(td)
        src = td_path / "input.pdf"
        shutil.copy2(source_path, src)
        original_size = source_path.stat().st_size
        out = td_path / "output.pdf"
        run_config = replace(config, output_dir=td_path)
        summary = compress_path(src, run_config, out)
        if not out.exists():
            failed = [r for r in summary.results if r.status == "failed"]
            detail = failed[0].error if failed else "Compression produced no output"
            logger.error("Compression produced no output for pdf %s: %s", pdf_id, detail)
            raise RuntimeError(detail)
        compressed_data = out.read_bytes()
        try:
            fitz = _fitz()
            with fitz.open(stream=compressed_data, filetype="pdf") as cdoc:
                ver_page_count = len(cdoc)
        except Exception as exc:
            logger.warning("Failed to count pages for compressed PDF %s: %s", pdf_id, exc)
            ver_page_count = 0

    compressed_size = len(compressed_data)
    ratio = 1.0 - (compressed_size / original_size) if original_size > 0 else None

    params = VersionParams(
        pdf_id=pdf_id,
        label=label,
        file_data=compressed_data,
        quality=config.quality,
        pdf_mode=config.pdf_mode,
        pdf_dpi=config.pdf_dpi,
        pdf_grayscale=config.pdf_grayscale,
        strip_metadata=config.strip_metadata,
        target_bytes=config.target_bytes,
        compression_ratio=ratio,
        page_count=ver_page_count,
    )
    return storage.add_version(params)


def _auto_label(target_bytes: Optional[int], quality: int, pdf_mode: str) -> str:
    parts = []
    if target_bytes is not None and target_bytes > 0:
        parts.append(format_size(target_bytes))
    parts.append(f"Q{quality}")
    parts.append(pdf_mode)
    return " · ".join(parts)
