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

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.background import BackgroundTask

from .core import compress_path
from .models import CompressionConfig
from .pdfs import _fitz, render_page
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

_storage: Optional[Storage] = None
_storage_lock = threading.Lock()


def _get_storage() -> Storage:
    global _storage
    if _storage is None:
        with _storage_lock:
            if _storage is None:
                data_dir = Path.home() / ".pdf-manager"
                _storage = Storage(data_dir)
    return _storage


def _sanitize_label(label: str) -> str:
    """Sanitize a label for use in download filenames."""
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", label)
    label = re.sub(r"-{2,}", "-", label).strip("- ")
    return label[:_MAX_LABEL_LEN]


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


# ── PDF CRUD ──

@app.get("/api/pdfs")
def api_list_pdfs() -> list:
    return [asdict(p) for p in _get_storage().list_pdfs_with_stats()]


@app.post("/api/pdfs/upload")
async def api_upload_pdf(
    file: UploadFile = File(...),
    quality: int = Form(82),
    target_size: Optional[str] = Form(None),
    pdf_mode: str = Form("auto"),
    pdf_dpi: int = Form(120),
    pdf_grayscale: bool = Form(False),
    strip_metadata: bool = Form(True),
    notes: str = Form(""),
):
    _validate_compress_params(quality, pdf_mode, pdf_dpi, target_size)
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
        doc = fitz.open(stream=data, filetype="pdf")
        page_count = len(doc)
        doc.close()
    except Exception:
        raise HTTPException(400, detail="Invalid PDF file")
    if page_count == 0:
        raise HTTPException(400, detail="PDF has no pages")

    pdf = storage.add_pdf(filename, data, page_count, notes)
    logger.info("Uploaded PDF %s (%s, %d pages)", filename, format_size(len(data)), page_count)
    result = asdict(pdf)
    result["warning"] = None

    target_bytes = parse_size(target_size)
    try:
        config = CompressionConfig(
            quality=quality, target_bytes=target_bytes, pdf_mode=pdf_mode,
            pdf_dpi=pdf_dpi, pdf_grayscale=pdf_grayscale, strip_metadata=strip_metadata,
        )
        _compress_and_store(storage, pdf.id, data, config, label="Initial compression")
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
        raise HTTPException(404, detail="File not found")
    try:
        return FileResponse(path, filename=pdf.filename, media_type="application/pdf")
    except FileNotFoundError:
        raise HTTPException(404, detail="File not found")


@app.get("/api/pdfs/{pdf_id}/versions")
def api_list_versions(pdf_id: str) -> list:
    storage = _get_storage()
    if not storage.get_pdf(pdf_id):
        raise HTTPException(404, detail="PDF not found")
    return [asdict(v) for v in storage.list_versions(pdf_id)]


@app.post("/api/pdfs/{pdf_id}/compress")
async def api_compress_pdf(
    pdf_id: str,
    quality: int = Form(82),
    target_size: Optional[str] = Form(None),
    pdf_mode: str = Form("auto"),
    pdf_dpi: int = Form(120),
    pdf_grayscale: bool = Form(False),
    strip_metadata: bool = Form(True),
    label: str = Form(""),
):
    _validate_compress_params(quality, pdf_mode, pdf_dpi, target_size)
    if len(label) > _MAX_LABEL_LEN:
        raise HTTPException(422, detail=f"label must be {_MAX_LABEL_LEN} characters or fewer")
    storage = _get_storage()
    pdf = storage.get_pdf(pdf_id)
    if not pdf:
        raise HTTPException(404, detail="PDF not found")
    path = storage.get_pdf_path(pdf_id)
    if not path:
        raise HTTPException(404, detail="Original file missing")
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise HTTPException(404, detail="Original file missing")
    target_bytes = parse_size(target_size)
    if not label:
        label = _auto_label(target_bytes, quality, pdf_mode)
    config = CompressionConfig(
        quality=quality, target_bytes=target_bytes, pdf_mode=pdf_mode,
        pdf_dpi=pdf_dpi, pdf_grayscale=pdf_grayscale, strip_metadata=strip_metadata,
    )

    try:
        ver = _compress_and_store(storage, pdf_id, data, config, label)
    except Exception as exc:
        logger.error("Compression failed for pdf %s: %s", pdf_id, exc)
        raise HTTPException(500, detail="Compression failed")
    logger.info("Compressed pdf %s: %s → %s (%.1f%% reduction)", pdf_id, format_size(len(data)), format_size(ver.file_size), (ver.compression_ratio or 0) * 100)
    return asdict(ver)


@app.post("/api/pdfs/batch-compress")
async def api_batch_compress(
    quality: int = Form(82),
    target_size: Optional[str] = Form(None),
    pdf_mode: str = Form("auto"),
    pdf_dpi: int = Form(120),
    pdf_grayscale: bool = Form(False),
    strip_metadata: bool = Form(True),
    pdf_ids: Optional[str] = Form(None),
    label: str = Form(""),
):
    _validate_compress_params(quality, pdf_mode, pdf_dpi, target_size)
    if len(label) > _MAX_LABEL_LEN:
        raise HTTPException(422, detail=f"label must be {_MAX_LABEL_LEN} characters or fewer")
    storage = _get_storage()
    all_pdfs = storage.list_pdfs()
    if pdf_ids:
        id_set = {s.strip() for s in pdf_ids.split(",") if s.strip()}
        pdfs = [p for p in all_pdfs if p.id in id_set]
    else:
        pdfs = all_pdfs
    if not pdfs:
        return {"compressed": 0, "results": []}

    target_bytes = parse_size(target_size)
    if not label:
        label = _auto_label(target_bytes, quality, pdf_mode)
    config = CompressionConfig(
        quality=quality, target_bytes=target_bytes, pdf_mode=pdf_mode,
        pdf_dpi=pdf_dpi, pdf_grayscale=pdf_grayscale, strip_metadata=strip_metadata,
    )
    logger.info("Batch compress: %d PDFs, quality=%d, mode=%s", len(pdfs), quality, pdf_mode)
    results = []
    for pdf in pdfs:
        path = storage.get_pdf_path(pdf.id)
        if not path:
            results.append({"pdf_id": pdf.id, "filename": pdf.filename, "status": "error", "error": "File missing from disk"})
            continue
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            results.append({"pdf_id": pdf.id, "filename": pdf.filename, "status": "error", "error": "File missing from disk"})
            continue
        try:
            ver = _compress_and_store(storage, pdf.id, data, config, label)
            results.append({"pdf_id": pdf.id, "filename": pdf.filename, "version_id": ver.id, "status": "ok",
                            "original_size": len(data), "compressed_size": ver.file_size, "compression_ratio": ver.compression_ratio})
        except Exception as exc:
            logger.warning("Batch compress failed for %s: %s", pdf.id, exc)
            results.append({"pdf_id": pdf.id, "filename": pdf.filename, "status": "error", "error": "Compression failed"})

    return {"compressed": len([r for r in results if r["status"] == "ok"]), "results": results}


@app.put("/api/pdfs/{pdf_id}/notes")
def api_update_notes(pdf_id: str, body: NotesUpdate) -> dict:
    storage = _get_storage()
    if not storage.update_notes(pdf_id, body.notes):
        raise HTTPException(404, detail="PDF not found")
    return {"ok": True}


@app.delete("/api/pdfs/{pdf_id}")
def api_delete_pdf(pdf_id: str) -> dict:
    storage = _get_storage()
    if not storage.delete_pdf(pdf_id):
        raise HTTPException(404, detail="PDF not found")
    logger.info("Deleted PDF %s", pdf_id)
    return {"ok": True}


class BatchDeleteRequest(BaseModel):
    pdf_ids: list[str]


@app.post("/api/pdfs/batch-delete")
def api_batch_delete(body: BatchDeleteRequest) -> dict:
    storage = _get_storage()
    deleted = storage.batch_delete_pdfs(body.pdf_ids)
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
        raise HTTPException(404, detail="Version file not found")
    pdf = storage.get_pdf(ver.pdf_id)
    stem = Path(pdf.filename).stem if pdf else "version"
    label = _sanitize_label(ver.label) if ver.label else ""
    download_name = f"{stem}_{label}.pdf" if label else f"{stem}.pdf"
    try:
        return FileResponse(path, filename=download_name, media_type="application/pdf")
    except FileNotFoundError:
        raise HTTPException(404, detail="Version file not found")


@app.delete("/api/versions/{version_id}")
def api_delete_version(version_id: str) -> dict:
    storage = _get_storage()
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
        total = pdf.page_count
    elif pdf_type == "version":
        ver = storage.get_version(item_id)
        if not ver:
            raise HTTPException(404, detail="Version not found")
        path = storage.get_version_path(item_id)
        pdf = storage.get_pdf(ver.pdf_id)
        total = pdf.page_count if pdf else 1
    else:
        raise HTTPException(400, detail="pdf_type must be 'original' or 'version'")
    if not path:
        raise HTTPException(404, detail="File not found")
    if page < 0 or page >= total:
        raise HTTPException(422, detail=f"Page {page} out of range (0-{total - 1})")
    try:
        png_data = render_page(path, page)
    except FileNotFoundError:
        raise HTTPException(404, detail="File not found")
    except Exception as exc:
        logger.error("Preview render failed for %s/%s page %d: %s", pdf_type, item_id, page, exc)
        raise HTTPException(500, detail="Preview render failed")

    return StreamingResponse(BytesIO(png_data), media_type="image/png")


_COPY_CHUNK = 1024 * 1024  # 1 MB


def _read_upload_with_limit(file: UploadFile) -> bytes:
    """Read an upload file in chunks, enforcing size limit before full allocation."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(_COPY_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(413, detail=f"File too large ({format_size(total)}). Maximum is {format_size(_MAX_UPLOAD_BYTES)}.")
        chunks.append(chunk)
    return b"".join(chunks)


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
                    raise HTTPException(413, detail=f"File too large ({safe_name}). Maximum is {_MAX_UPLOAD_BYTES // (1024*1024)} MB.")
                handle.write(chunk)


# ── Legacy compress endpoint (backward compat) ──

@app.post("/compress")
async def compress_upload(
    files: list[UploadFile] = File(...),
    quality: int = Form(82),
    max_edge: Optional[int] = Form(None),
    target_size: Optional[str] = Form(None),
    pdf_mode: str = Form("auto"),
    pdf_dpi: int = Form(120),
    to_webp: bool = Form(False),
    pdf_grayscale: bool = Form(False),
    strip_metadata: bool = Form(True),
    archive: Optional[str] = Form(None),
) -> FileResponse:
    _validate_compress_params(quality, pdf_mode, pdf_dpi, target_size)
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
    except HTTPException:
        shutil.rmtree(temp_path, True)
        raise
    config = CompressionConfig(
        quality=quality, max_edge=max_edge, to_webp=to_webp,
        target_bytes=parse_size(target_size), output_dir=output_dir,
        archive=archive, pdf_mode=pdf_mode, pdf_dpi=pdf_dpi,
        pdf_grayscale=pdf_grayscale, strip_metadata=strip_metadata,
    )
    try:
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
        logger.error("Legacy compress failed: %s", exc)
        raise HTTPException(500, detail="Compression failed")
    response = FileResponse(result.output, filename=result.output.name, media_type="application/octet-stream")
    response.background = BackgroundTask(shutil.rmtree, temp_path, True)
    return response


# ── Helpers ──


def _compress_and_store(
    storage: Storage,
    pdf_id: str,
    original_data: bytes,
    config: CompressionConfig,
    label: str,
) -> VersionRecord:
    with tempfile.TemporaryDirectory(prefix="pdf_compress_") as td:
        td_path = Path(td)
        src = td_path / "input.pdf"
        src.write_bytes(original_data)
        out = td_path / "output.pdf"
        run_config = replace(config, output_dir=td_path)
        summary = compress_path(src, run_config, out)
        if not out.exists():
            failed = [r for r in summary.results if r.status == "failed"]
            detail = failed[0].error if failed else "Compression produced no output"
            logger.error("Compression produced no output for pdf %s: %s", pdf_id, detail)
            raise RuntimeError(detail)
        compressed_data = out.read_bytes()

    original_size = len(original_data)
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
    )
    return storage.add_version(params)


def _auto_label(target_bytes: Optional[int], quality: int, pdf_mode: str) -> str:
    parts = []
    if target_bytes:
        parts.append(format_size(target_bytes))
    parts.append(f"Q{quality}")
    parts.append(pdf_mode)
    return " · ".join(parts)
