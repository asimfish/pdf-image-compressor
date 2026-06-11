from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pdf_manager")

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

from .core import compress_path
from .models import CompressionConfig
from .pdfs import _fitz, evict_render_cache, render_page
from .storage import Storage, VersionParams, VersionRecord
from .utils import clamp_quality, format_size, parse_size, unique_path

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="PDF Manager")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

_VALID_MODES = {"auto", "optimize", "raster"}
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
_MAX_NOTES_LEN = 5000
_MAX_LABEL_LEN = 500


def _validate_compress_params(quality: int, pdf_mode: str, pdf_dpi: int, target_size: Optional[str] = None) -> None:
    if clamp_quality(quality) != quality:
        raise HTTPException(422, detail="quality must be between 1 and 95")
    if pdf_mode not in _VALID_MODES:
        raise HTTPException(422, detail=f"pdf_mode must be one of {sorted(_VALID_MODES)}")
    if not 36 <= pdf_dpi <= 300:
        raise HTTPException(422, detail="pdf_dpi must be between 36 and 300")
    if target_size:
        try:
            parse_size(target_size)
        except ValueError:
            raise HTTPException(422, detail="Invalid target_size format")


def _compress_form(
    quality: int = Form(82),
    target_size: Optional[str] = Form(None),
    pdf_mode: str = Form("auto"),
    pdf_dpi: int = Form(120),
    pdf_grayscale: bool = Form(False),
    strip_metadata: bool = Form(True),
) -> CompressionConfig:
    _validate_compress_params(quality, pdf_mode, pdf_dpi, target_size)
    return CompressionConfig(
        quality=quality, target_bytes=parse_size(target_size), pdf_mode=pdf_mode,
        pdf_dpi=pdf_dpi, pdf_grayscale=pdf_grayscale, strip_metadata=strip_metadata,
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
    if isinstance(exc, (FileNotFoundError, PermissionError, OSError)):
        return "File access error"
    if "no output" in msg.lower():
        return "Compression produced no output — the file may be corrupted"
    if "corrupt" in msg.lower() or "invalid" in msg.lower():
        return "The file appears to be corrupted or invalid"
    sanitized = _PATH_RE.sub("[path]", msg)
    if sanitized != msg:
        return "Compression failed"
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
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


# ── Health ──

@app.get("/api/health")
def api_health() -> dict:
    storage = _get_storage()
    stats = storage.stats()
    return {"status": "ok", "pdf_count": stats["pdf_count"], "version_count": stats["version_count"]}


# ── Stats ──

@app.get("/api/stats")
def api_stats() -> dict:
    return _get_storage().stats()


@app.get("/api/config")
def api_config() -> dict:
    return {"max_upload_bytes": _MAX_UPLOAD_BYTES}


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

    pdf = storage.add_pdf(filename, data, page_count, notes)
    logger.info("Uploaded PDF %s (%s, %d pages)", filename, format_size(len(data)), page_count)
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


# ── Legacy compress endpoint (backward compat) ──

@app.post("/compress")
def compress_upload(
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
    temp = tempfile.mkdtemp(prefix="file_compressor_web_")
    temp_path = Path(temp)
    upload_dir = temp_path / "uploads"
    output_dir = temp_path / "compressed"
    upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        _save_upload_files(files, upload_dir)
        config = replace(config, max_edge=max_edge, to_webp=to_webp, output_dir=output_dir, archive=archive)
        if len(files) > 1 or archive == "zip":
            source = upload_dir
        else:
            source = next(upload_dir.iterdir(), None)
            if source is None:
                raise HTTPException(400, detail="No files uploaded")
        output = output_dir / "compressed.zip" if archive == "zip" else None
        summary = compress_path(source, config, output)
        result = summary.archive or next((item for item in summary.results if item.output is not None), None)
        if result is None or result.output is None:
            raise HTTPException(500, detail="Compression produced no output")
    except HTTPException:
        shutil.rmtree(temp_path, True)
        raise
    except Exception as exc:
        shutil.rmtree(temp_path, True)
        logger.error("Legacy compress failed: %s", _sanitize_error(exc))
        raise HTTPException(500, detail="Compression failed")
    response = FileResponse(result.output, filename=result.output.name, media_type="application/octet-stream")
    response.background = BackgroundTask(shutil.rmtree, temp_path, True)
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
    if target_bytes:
        parts.append(format_size(target_bytes))
    parts.append(f"Q{quality}")
    parts.append(pdf_mode)
    return " · ".join(parts)
