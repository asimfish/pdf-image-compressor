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
    strip_metadata: bool
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
    strip_metadata INTEGER NOT NULL DEFAULT 1,
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
        self._migrate()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    _EXPECTED_COLUMNS: list[tuple[str, str]] = [
        ("versions", "strip_metadata INTEGER NOT NULL DEFAULT 1"),
    ]

    def _migrate(self) -> None:
        """Add missing columns for backward compatibility with older databases."""
        for table, col_def in self._EXPECTED_COLUMNS:
            col_name = col_def.split()[0]
            cols = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if col_name not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                self._conn.commit()

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

    def list_pdfs_with_stats(self) -> list[dict]:
        rows = self._conn.execute("""
            SELECT p.*,
                   COALESCE(vc.cnt, 0) AS version_count,
                   bv.best_id, bv.best_size, bv.best_ratio
            FROM pdfs p
            LEFT JOIN (SELECT pdf_id, COUNT(*) AS cnt FROM versions GROUP BY pdf_id) vc ON p.id = vc.pdf_id
            LEFT JOIN (
                SELECT pdf_id, id AS best_id, file_size AS best_size, compression_ratio AS best_ratio
                FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY file_size, created_at DESC) AS rn
                    FROM versions
                ) ranked WHERE rn = 1
            ) bv ON p.id = bv.pdf_id
            ORDER BY p.upload_time DESC
        """).fetchall()
        return [
            {
                "id": r["id"], "filename": r["filename"], "file_size": r["file_size"],
                "page_count": r["page_count"], "upload_time": r["upload_time"], "notes": r["notes"],
                "version_count": r["version_count"],
                "best_compression_ratio": r["best_ratio"],
                "best_compressed_size": r["best_size"],
                "best_version_id": r["best_id"],
            }
            for r in rows
        ]

    def get_pdf_path(self, pdf_id: str) -> Optional[Path]:
        row = self._conn.execute("SELECT file_path FROM pdfs WHERE id=?", (pdf_id,)).fetchone()
        return Path(row["file_path"]) if row else None

    def delete_pdf(self, pdf_id: str) -> bool:
        deleted = self._delete_pdf_no_commit(pdf_id)
        if deleted:
            self._conn.commit()
        return deleted

    def _delete_pdf_no_commit(self, pdf_id: str) -> bool:
        pdf_path = self.get_pdf_path(pdf_id)
        versions = self.list_versions(pdf_id)
        for v in versions:
            vpath = self.get_version_path(v.id)
            if vpath and vpath.exists():
                vpath.unlink()
        if pdf_path and pdf_path.exists():
            pdf_path.unlink()
        cur = self._conn.execute("DELETE FROM pdfs WHERE id=?", (pdf_id,))
        return cur.rowcount > 0

    def batch_delete_pdfs(self, pdf_ids: list[str]) -> int:
        deleted = 0
        try:
            for pdf_id in pdf_ids:
                if self._delete_pdf_no_commit(pdf_id):
                    deleted += 1
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return deleted

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
        strip_metadata: bool = True,
    ) -> VersionRecord:
        ver_id = uuid.uuid4().hex[:12]
        file_path = self._versions / f"{ver_id}.pdf"
        file_path.write_bytes(file_data)
        now = _now_iso()
        self._conn.execute(
            "INSERT INTO versions (id,pdf_id,label,file_path,file_size,quality,pdf_mode,pdf_dpi,pdf_grayscale,strip_metadata,target_bytes,compression_ratio,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ver_id, pdf_id, label, str(file_path), len(file_data), quality, pdf_mode, pdf_dpi, int(pdf_grayscale), int(strip_metadata), target_bytes, compression_ratio, now),
        )
        self._conn.commit()
        return VersionRecord(
            id=ver_id, pdf_id=pdf_id, label=label, file_size=len(file_data),
            quality=quality, pdf_mode=pdf_mode, pdf_dpi=pdf_dpi, pdf_grayscale=bool(pdf_grayscale),
            strip_metadata=bool(strip_metadata),
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
        row = self._conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM pdfs) AS pdf_count,
                (SELECT COUNT(*) FROM versions) AS version_count,
                (SELECT COALESCE(SUM(file_size), 0) FROM pdfs) AS total_original_bytes,
                (SELECT COALESCE(SUM(file_size), 0) FROM versions) AS total_compressed_bytes,
                (SELECT COALESCE(SUM(p.file_size - best.best_size), 0)
                 FROM pdfs p
                 JOIN (SELECT pdf_id, MIN(file_size) AS best_size FROM versions GROUP BY pdf_id) best
                 ON p.id = best.pdf_id) AS total_saved_bytes
        """).fetchone()
        return {
            "pdf_count": row[0],
            "version_count": row[1],
            "total_original_bytes": row[2],
            "total_compressed_bytes": row[3],
            "total_saved_bytes": row[4],
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
        strip_metadata=bool(row["strip_metadata"]),
        target_bytes=row["target_bytes"],
        compression_ratio=row["compression_ratio"],
        created_at=row["created_at"],
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
