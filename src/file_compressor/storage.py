from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PdfRecord:
    id: str
    filename: str
    file_size: int
    page_count: int
    upload_time: str
    notes: str


@dataclass(frozen=True)
class VersionRecord:
    id: str
    pdf_id: str
    label: str
    file_size: int
    quality: int
    pdf_mode: str
    pdf_dpi: int
    pdf_grayscale: bool
    target_bytes: Optional[int]
    compression_ratio: Optional[float]
    created_at: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS pdfs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    upload_time TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY,
    pdf_id TEXT NOT NULL REFERENCES pdfs(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    quality INTEGER NOT NULL,
    pdf_mode TEXT NOT NULL DEFAULT 'auto',
    pdf_dpi INTEGER NOT NULL DEFAULT 120,
    pdf_grayscale INTEGER NOT NULL DEFAULT 0,
    target_bytes INTEGER,
    compression_ratio REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_versions_pdf ON versions(pdf_id);
"""


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir.expanduser().resolve()
        self._originals = self._data_dir / "originals"
        self._versions = self._data_dir / "versions"
        self._originals.mkdir(parents=True, exist_ok=True)
        self._versions.mkdir(parents=True, exist_ok=True)
        db_path = self._data_dir / "library.db"
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ── PDF CRUD ──

    def add_pdf(self, filename: str, file_data: bytes, page_count: int, notes: str = "") -> PdfRecord:
        pdf_id = uuid.uuid4().hex[:12]
        suffix = Path(filename).suffix.lower()
        stored_name = f"{pdf_id}{suffix}"
        file_path = self._originals / stored_name
        file_path.write_bytes(file_data)
        now = _now_iso()
        self._conn.execute(
            "INSERT INTO pdfs (id, filename, file_path, file_size, page_count, upload_time, notes) VALUES (?,?,?,?,?,?,?)",
            (pdf_id, filename, str(file_path), len(file_data), page_count, now, notes),
        )
        self._conn.commit()
        return PdfRecord(id=pdf_id, filename=filename, file_size=len(file_data), page_count=page_count, upload_time=now, notes=notes)

    def get_pdf(self, pdf_id: str) -> Optional[PdfRecord]:
        row = self._conn.execute("SELECT * FROM pdfs WHERE id=?", (pdf_id,)).fetchone()
        return _row_to_pdf(row) if row else None

    def list_pdfs(self) -> list[PdfRecord]:
        rows = self._conn.execute("SELECT * FROM pdfs ORDER BY upload_time DESC").fetchall()
        return [_row_to_pdf(r) for r in rows]

    def pdf_version_counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT pdf_id, COUNT(*) AS cnt FROM versions GROUP BY pdf_id").fetchall()
        return {row["pdf_id"]: row["cnt"] for row in rows}

    def pdf_best_versions(self) -> dict[str, dict]:
        rows = self._conn.execute("""
            SELECT v.pdf_id, v.id AS best_id, v.file_size AS best_size, v.compression_ratio AS best_ratio
            FROM versions v
            INNER JOIN (SELECT pdf_id, MIN(file_size) AS min_size FROM versions GROUP BY pdf_id) m
            ON v.pdf_id = m.pdf_id AND v.file_size = m.min_size
        """).fetchall()
        return {row["pdf_id"]: {"best_id": row["best_id"], "best_size": row["best_size"], "best_ratio": row["best_ratio"]} for row in rows}

    def get_pdf_path(self, pdf_id: str) -> Optional[Path]:
        row = self._conn.execute("SELECT file_path FROM pdfs WHERE id=?", (pdf_id,)).fetchone()
        return Path(row["file_path"]) if row else None

    def delete_pdf(self, pdf_id: str) -> bool:
        pdf_path = self.get_pdf_path(pdf_id)
        versions = self.list_versions(pdf_id)
        for v in versions:
            vpath = self.get_version_path(v.id)
            if vpath and vpath.exists():
                vpath.unlink()
        if pdf_path and pdf_path.exists():
            pdf_path.unlink()
        cur = self._conn.execute("DELETE FROM pdfs WHERE id=?", (pdf_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def update_notes(self, pdf_id: str, notes: str) -> bool:
        cur = self._conn.execute("UPDATE pdfs SET notes=? WHERE id=?", (notes, pdf_id))
        self._conn.commit()
        return cur.rowcount > 0

    # ── Version CRUD ──

    def add_version(
        self,
        pdf_id: str,
        label: str,
        file_data: bytes,
        quality: int,
        pdf_mode: str,
        pdf_dpi: int,
        pdf_grayscale: bool,
        target_bytes: Optional[int],
        compression_ratio: Optional[float],
    ) -> VersionRecord:
        ver_id = uuid.uuid4().hex[:12]
        file_path = self._versions / f"{ver_id}.pdf"
        file_path.write_bytes(file_data)
        now = _now_iso()
        self._conn.execute(
            "INSERT INTO versions (id,pdf_id,label,file_path,file_size,quality,pdf_mode,pdf_dpi,pdf_grayscale,target_bytes,compression_ratio,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ver_id, pdf_id, label, str(file_path), len(file_data), quality, pdf_mode, pdf_dpi, int(pdf_grayscale), target_bytes, compression_ratio, now),
        )
        self._conn.commit()
        return VersionRecord(
            id=ver_id, pdf_id=pdf_id, label=label, file_size=len(file_data),
            quality=quality, pdf_mode=pdf_mode, pdf_dpi=pdf_dpi, pdf_grayscale=bool(pdf_grayscale),
            target_bytes=target_bytes, compression_ratio=compression_ratio, created_at=now,
        )

    def list_versions(self, pdf_id: str) -> list[VersionRecord]:
        rows = self._conn.execute("SELECT * FROM versions WHERE pdf_id=? ORDER BY created_at DESC", (pdf_id,)).fetchall()
        return [_row_to_version(r) for r in rows]

    def get_version_path(self, version_id: str) -> Optional[Path]:
        row = self._conn.execute("SELECT file_path FROM versions WHERE id=?", (version_id,)).fetchone()
        return Path(row["file_path"]) if row else None

    def delete_version(self, version_id: str) -> bool:
        vpath = self.get_version_path(version_id)
        if vpath and vpath.exists():
            vpath.unlink()
        cur = self._conn.execute("DELETE FROM versions WHERE id=?", (version_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def stats(self) -> dict:
        pdf_count = self._conn.execute("SELECT COUNT(*) FROM pdfs").fetchone()[0]
        version_count = self._conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        total_original = self._conn.execute("SELECT COALESCE(SUM(file_size),0) FROM pdfs").fetchone()[0]
        total_compressed = self._conn.execute("SELECT COALESCE(SUM(file_size),0) FROM versions").fetchone()[0]
        # Sum of (original - best_version) per PDF that has at least one version
        saved = self._conn.execute("""
            SELECT COALESCE(SUM(p.file_size - best.best_size), 0)
            FROM pdfs p
            JOIN (SELECT pdf_id, MIN(file_size) AS best_size FROM versions GROUP BY pdf_id) best
            ON p.id = best.pdf_id
        """).fetchone()[0]
        return {
            "pdf_count": pdf_count,
            "version_count": version_count,
            "total_original_bytes": total_original,
            "total_compressed_bytes": total_compressed,
            "total_saved_bytes": saved,
        }


def _row_to_pdf(row: sqlite3.Row) -> PdfRecord:
    return PdfRecord(
        id=row["id"],
        filename=row["filename"],
        file_size=row["file_size"],
        page_count=row["page_count"],
        upload_time=row["upload_time"],
        notes=row["notes"],
    )


def _row_to_version(row: sqlite3.Row) -> VersionRecord:
    return VersionRecord(
        id=row["id"],
        pdf_id=row["pdf_id"],
        label=row["label"],
        file_size=row["file_size"],
        quality=row["quality"],
        pdf_mode=row["pdf_mode"],
        pdf_dpi=row["pdf_dpi"],
        pdf_grayscale=bool(row["pdf_grayscale"]),
        target_bytes=row["target_bytes"],
        compression_ratio=row["compression_ratio"],
        created_at=row["created_at"],
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
